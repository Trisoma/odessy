"""Does the pinned runtime keep a session across processes, and what does resume expose?

Prompt 10 needs durable history, so the first thing to establish is what the **pin** actually
does — not what its documentation suggests. This probe answers, against the real 0.2.1rc2
executable and a deterministic provider:

1. After `session/new` + a completed turn + `session/close` + process exit, does a **new
   runtime process** with the same `DSH_HOME` resume that session id?
2. Does the resumed turn see the earlier conversation (durable model context), and does the
   client receive the earlier messages as updates (durable *client* history)?
3. What does `session/list` contain before and after a restart?
4. What is the error shape for an unknown session id, and for a mismatched cwd?
5. Is a session that was **never closed** (an interrupted turn) resumable?

Every answer is recorded, including the ones that say "no", because the Odessy design has to be
built on what the runtime owns rather than on what it would be convenient for it to expose.

    python probe_sessions.py --exe <pinned exe> --home <DSH_HOME> --work <dir>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mock_provider  # noqa: E402
from acp_client import AcpClient, AcpError  # noqa: E402

MODEL = "mock-model"
PLATFORM = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
            "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA")


def environment(home: Path, base_url: str) -> dict[str, str]:
    environ = {name: os.environ[name] for name in PLATFORM if name in os.environ}
    environ.update({
        "DSH_HOME": str(home),
        "DEEPSEEK_BASE_URL": base_url,
        "DEEPSEEK_API_KEY": "mock-key-not-a-secret",
    })
    return environ


def requests(work: Path) -> list[dict]:
    path = work / "mock" / "requests.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def describe(error: BaseException) -> dict:
    if isinstance(error, AcpError):
        return {"error": "AcpError", "code": error.code, "message": str(error)[:300]}
    return {"error": type(error).__name__, "message": str(error)[:300]}


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
    base_url = f"http://127.0.0.1:{port}/v1"
    env = environment(home, base_url)
    exe = str(Path(args.exe).resolve())
    try:
        # ---- 1: a completed turn in one process --------------------------------
        first = AcpClient(exe, env=env, cwd=str(workspace))
        first.initialize()
        started = first.new_session(cwd=str(workspace))
        session_id = started["sessionId"]
        findings["session_id"] = session_id
        turn = first.prompt(session_id, "remember zeta-42")
        findings["first_turn"] = {"stop_reason": turn.stop_reason,
                                  "updates": len(turn.updates)}
        findings["messages_before_restart"] = [
            {"n_messages": item.get("n_messages"), "roles": item.get("message_roles")}
            for item in requests(work)]
        findings["list_before_restart"] = first.call("session/list", {})
        first.close_session(session_id)
        first.close()

        # ---- 2: the same session from a *new* process --------------------------
        second = AcpClient(exe, env=env, cwd=str(workspace))
        second.initialize()
        findings["list_after_restart"] = second.call("session/list", {})
        try:
            resumed = second.resume_session(session_id, cwd=str(workspace))
            findings["resume"] = {"ok": True, "result_keys": sorted(resumed or {})}
        except BaseException as error:  # noqa: BLE001 - the failure shape is the finding
            findings["resume"] = {"ok": False, **describe(error)}
            resumed = None
        if resumed is not None:
            after = second.prompt(session_id, "what number did I say")
            findings["resumed_turn"] = {"stop_reason": after.stop_reason,
                                        "updates": len(after.updates),
                                        "update_kinds": sorted({
                                            (u.get("update") or {}).get("sessionUpdate")
                                            for u in after.updates if isinstance(u, dict)})}
            findings["messages_after_resume"] = [
                {"n_messages": item.get("n_messages"), "roles": item.get("message_roles"),
                 "last_user": item.get("last_user")}
                for item in requests(work)][len(findings["messages_before_restart"]):]
        second.close()

        # ---- 3: unknown id, and a mismatched cwd -------------------------------
        third = AcpClient(exe, env=env, cwd=str(workspace))
        third.initialize()
        try:
            third.resume_session(str(uuid.uuid4()), cwd=str(workspace))
            findings["unknown_session"] = {"refused": False}
        except BaseException as error:  # noqa: BLE001
            findings["unknown_session"] = {"refused": True, **describe(error)}
        other = workspace.parent / "other-ws"
        other.mkdir(exist_ok=True)
        try:
            third.resume_session(session_id, cwd=str(other))
            findings["other_cwd"] = {"refused": False}
        except BaseException as error:  # noqa: BLE001
            findings["other_cwd"] = {"refused": True, **describe(error)}
        third.close()

        # ---- 4: a session whose process died mid-turn --------------------------
        fourth = AcpClient(exe, env=env, cwd=str(workspace))
        fourth.initialize()
        crashed = fourth.new_session(cwd=str(workspace))["sessionId"]
        handle = fourth.prompt_async(crashed, "please slow cancel")
        handle.wait(2.0)
        fourth.close()  # process goes away with a turn in flight, no session/close
        fifth = AcpClient(exe, env=env, cwd=str(workspace))
        fifth.initialize()
        try:
            result = fifth.resume_session(crashed, cwd=str(workspace))
            findings["interrupted_resume"] = {"ok": True, "result_keys": sorted(result or {})}
            try:
                settled = fifth.prompt(crashed, "are you there")
                findings["interrupted_turn_after_resume"] = {
                    "stop_reason": settled.stop_reason, "updates": len(settled.updates)}
            except BaseException as error:  # noqa: BLE001
                findings["interrupted_turn_after_resume"] = describe(error)
        except BaseException as error:  # noqa: BLE001
            findings["interrupted_resume"] = {"ok": False, **describe(error)}
        fifth.close()
    finally:
        server.shutdown()

    print(json.dumps(findings, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
