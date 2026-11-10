"""What does the pin do when durable history is corrupt, newer, missing, or contended?

`probe_sessions.py` established that `session/resume` restores model context across processes
and that history is *not* replayed to the client. Prompt 10 also has to classify the unhappy
paths, and guessing those from the docs is not acceptable: Odessy must refuse clearly instead of
silently starting a fresh session under an existing identity. So this probe asks the runtime
directly:

* corrupt log bytes        -> what error, or does it resume with a truncated history?
* a log of a *newer* format (`session.v5.jsonl.zstd`) -> refuse, or ignore and start empty?
* a missing log file       -> error shape (same as unknown id?)
* two processes resuming one id at once -> does the second get a copy, or a shared session?

The last one matters most: if a second process can silently attach to the same durable session,
Odessy needs its own single-owner rule; if it gets refused, the runtime already enforces one.

    python probe_recovery.py --exe <pinned exe> --home <DSH_HOME> --work <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mock_provider  # noqa: E402
from acp_client import AcpClient, AcpError  # noqa: E402

PLATFORM = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
            "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA")


def environment(home: Path, base_url: str) -> dict[str, str]:
    environ = {name: os.environ[name] for name in PLATFORM if name in os.environ}
    environ.update({"DSH_HOME": str(home), "DEEPSEEK_BASE_URL": base_url,
                    "DEEPSEEK_API_KEY": "mock-key-not-a-secret"})
    return environ


def describe(error: BaseException) -> dict:
    if isinstance(error, AcpError):
        return {"refused": True, "code": error.code, "message": str(error)[:300]}
    return {"refused": True, "error": type(error).__name__, "message": str(error)[:300]}


def log_file(home: Path, session_id: str) -> Path | None:
    for path in home.rglob(f"{session_id}/*"):
        if path.is_file() and path.name.startswith("session."):
            return path
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    workspace = work / "ws"
    for path in (work / "mock", workspace, home):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")

    findings: dict = {}
    server, port = mock_provider.serve(work / "mock")
    env = environment(home, f"http://127.0.0.1:{port}/v1")
    exe = str(Path(args.exe).resolve())

    def seed(prompt: str) -> str:
        """A settled session with one completed turn, process gone."""
        client = AcpClient(exe, env=env, cwd=str(workspace))
        client.initialize()
        session_id = client.new_session(cwd=str(workspace))["sessionId"]
        client.prompt(session_id, prompt)
        client.close_session(session_id)
        client.close()
        return session_id

    def resume_case(label: str, session_id: str) -> dict:
        client = AcpClient(exe, env=env, cwd=str(workspace))
        client.initialize()
        try:
            result = client.resume_session(session_id, cwd=str(workspace))
            outcome: dict = {"ok": True, "result_keys": sorted(result or {})}
            try:
                turn = client.prompt(session_id, "do you remember the number")
                outcome["turn"] = {"stop_reason": turn.stop_reason}
            except BaseException as error:  # noqa: BLE001
                outcome["turn"] = describe(error)
            return outcome
        except BaseException as error:  # noqa: BLE001
            return {"ok": False, **describe(error)}
        finally:
            client.close()

    try:
        # ---- corrupt the durable log ------------------------------------------
        corrupt_id = seed("remember alpha-11")
        target = log_file(home, corrupt_id)
        findings["log_file"] = {"name": target.name if target else None,
                                "bytes": target.stat().st_size if target else None}
        if target is not None:
            payload = bytearray(target.read_bytes())
            for index in range(min(64, len(payload))):
                payload[index] = 0x00
            target.write_bytes(bytes(payload))
            findings["corrupt_head"] = resume_case("corrupt-head", corrupt_id)
            target.write_bytes(bytes(payload) + b"\x00\x00\x00\x00garbage-trailing")

        # ---- a log from a *newer* runtime format -------------------------------
        newer_id = seed("remember beta-22")
        newer = log_file(home, newer_id)
        findings["newer_format"] = {"renamed": False}
        if newer is not None:
            renamed = newer.with_name("session.v9.jsonl.zstd")
            newer.rename(renamed)
            findings["newer_format"] = {"renamed": True, "name": renamed.name,
                                        **resume_case("newer-format", newer_id)}
            renamed.rename(newer)

        # ---- missing log entirely (history pruned / home lost) -----------------
        pruned_id = seed("remember gamma-33")
        pruned = log_file(home, pruned_id)
        if pruned is not None:
            pruned.unlink()
        findings["missing_log"] = resume_case("missing-log", pruned_id)

        # ---- two processes resuming one id at the same time --------------------
        shared_id = seed("remember delta-44")
        owner = AcpClient(exe, env=env, cwd=str(workspace))
        owner.initialize()
        try:
            owner.resume_session(shared_id, cwd=str(workspace))
            second = AcpClient(exe, env=env, cwd=str(workspace))
            second.initialize()
            try:
                second.resume_session(shared_id, cwd=str(workspace))
                findings["concurrent_resume"] = {"second_refused": False}
                try:
                    findings["concurrent_resume"]["second_turn"] = {
                        "stop_reason": second.prompt(shared_id, "second writer").stop_reason}
                except BaseException as error:  # noqa: BLE001
                    findings["concurrent_resume"]["second_turn"] = describe(error)
            except BaseException as error:  # noqa: BLE001
                findings["concurrent_resume"] = {"second_refused": True, **describe(error)}
            finally:
                second.close()
            try:
                findings["concurrent_resume"]["owner_turn_after"] = {
                    "stop_reason": owner.prompt(shared_id, "first writer again").stop_reason}
            except BaseException as error:  # noqa: BLE001
                findings["concurrent_resume"]["owner_turn_after"] = describe(error)
        finally:
            owner.close()

        # ---- session/list shape ------------------------------------------------
        client = AcpClient(exe, env=env, cwd=str(workspace))
        client.initialize()
        findings["list"] = client.call("session/list", {})
        client.close()
    finally:
        server.shutdown()

    print(json.dumps(findings, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
