"""Clarification round trip against the pinned runtime (prompt 07).

This is the *real* path, not a fake:

    SessionController(engine="harness")
      -> HarnessRuntime (pinned binary, narrow profile plus the clarification tool row)
      -> LiveBridge (production socket client)
      -> packaged plugin (``experiments/.../plugin/odessy-stream-bridge``)
      -> the runtime's own ``@deepseek-ai/dsh-tool-ask-user`` tool
      -> InteractionBroker (the same broker and HTTP route the desktop answers)
      -> the human's answer back to the waiting tool
      -> the same turn continues and settles.

A deterministic local OpenAI-compatible provider asks the question once and continues
after the tool result, so nothing depends on a real model or network.

    python smoke_p07.py --exe <pin> --home <DSH_HOME> --work <dir> [--toolchain <dir>]

The plugin must be installed in ``--home``; pass ``--toolchain`` to (re)install it when the
installed copy differs from this source tree.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import mock_provider  # noqa: E402

from odessy.application.session import SessionController  # noqa: E402
from odessy.runtime_backends import TurnCancelled  # noqa: E402

MODEL = "mock-model"
PROMPT = "question please"           # the fixture asks, then continues
STOP_PROMPT = "question stop"        # the fixture asks, and the runner Stops
CONTINUED = "Answer received; continuing."
PLUGIN = HERE / "plugin" / "odessy-stream-bridge"
PLATFORM = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
            "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def installed_plugin_is_current(home: Path, profile: str = "acp") -> tuple[bool, str]:
    installed = home / "profiles" / profile / "node_modules" / "@odessy" / "dsh-stream-bridge"
    if not installed.exists():
        return False, "the bundle is not installed"
    for relative in ("lib/index.js", "cordis.patch.yml"):
        source, target = PLUGIN / relative, installed / relative
        if not target.exists() or _digest(source) != _digest(target):
            return False, f"{relative} differs from this source tree"
    return True, "installed copy matches this source tree"


def install_plugin(exe: str, home: Path, toolchain: Path, profile: str = "acp") -> tuple[bool, str]:
    command = [sys.executable, str(HERE / "install_plugin.py"), "--exe", exe,
               "--home", str(home), "--toolchain", str(toolchain), "--profile", profile]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    detail = (completed.stdout + completed.stderr).strip().splitlines()
    return completed.returncode == 0, (detail[-1] if detail else f"exit {completed.returncode}")


def _alive(pid):
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True, check=False).stdout
    return str(pid) in out


def _profile(port: int) -> dict:
    return {"name": "custom", "adapter": "cloud", "provider": "custom",
            "model": MODEL, "endpoint": f"http://127.0.0.1:{port}/v1"}


def _make_session(profile: dict) -> SessionController:
    return SessionController(
        model_factory=lambda name: object(),
        agent_factory=lambda *args, **kwargs: None,
        profiles={"custom": profile},
        selected_profile="custom",
        runtime_selection="harness",
    )


async def _wait_for_question(session: SessionController, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = session.pending_questions
        if pending:
            return pending[0]
        await asyncio.sleep(0.02)
    raise AssertionError("no clarification question reached the broker")


def _requests(work: Path) -> list[dict]:
    path = work / "mock" / "requests.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--toolchain", default="")
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    workspace = work / "ws"
    for path in (work / "mock", workspace, home):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")

    checks: list[tuple[str, bool, str]] = []
    server, port = mock_provider.serve(work / "mock")
    try:
        current, detail = installed_plugin_is_current(home)
        if not current and args.toolchain:
            current, detail = install_plugin(args.exe, home, Path(args.toolchain).resolve())
        checks.append(("the packaged question bridge is installed", current, detail))
        if not current:
            raise SystemExit("install the plugin first (pass --toolchain)")

        exe = str(Path(args.exe).resolve())
        for name in PLATFORM:
            if name in os.environ:
                os.environ.setdefault(name, os.environ[name])
        os.environ.update({
            "ODESSY_HARNESS_EXE": exe,
            "ODESSY_HARNESS_HOME": str(home),
            "ODESSY_HARNESS_WORKSPACE": str(workspace),
            "ODESSY_CLOUD_API_KEY": "mock-key-not-a-secret",
        })
        profile = _profile(port)

        async def answered_turn():
            session = _make_session(profile)
            try:
                turn = asyncio.create_task(session.run_turn(PROMPT))
                entry = await _wait_for_question(session)
                seen = {"prompt": entry.get("prompt"), "choices": entry.get("choices"),
                        "risk": entry.get("risk"), "pending": len(session.pending_questions)}
                session.answer_question(entry["id"], "Yes")
                result = await asyncio.wait_for(turn, 90)
                pid = session.runtime._process.pid if session.runtime._process else None
                return session, seen, result, pid
            finally:
                await session.close()

        session, seen, result, pid = asyncio.run(answered_turn())
        checks.append(("the question reached the broker with its text",
                       seen["prompt"] == "Proceed with the cleanup?", json.dumps(seen)))
        checks.append(("the offered choices reached the broker",
                       seen["choices"] == ["Yes", "No"], json.dumps(seen["choices"])))
        checks.append(("exactly one question was pending",
                       seen["pending"] == 1, str(seen["pending"])))
        checks.append(("the same turn continued after the answer",
                       result.final_text == CONTINUED, result.final_text))
        checks.append(("the tool was available to the model",
                       any("ask_user_question" in (item.get("tools") or [])
                           for item in _requests(work)),
                       json.dumps(_requests(work)[-1].get("tools") if _requests(work) else [])))
        checks.append(("no other model-facing tool was mounted",
                       all(set(item.get("tools") or []) <= {"ask_user_question"}
                           for item in _requests(work)),
                       json.dumps(sorted({name for item in _requests(work)
                                          for name in item.get("tools") or []}))))
        checks.append(("the model saw the tool result and continued",
                       any("tool" in (item.get("message_roles") or [])
                           for item in _requests(work)[1:]),
                       json.dumps([item.get("message_roles") for item in _requests(work)])))
        checks.append(("the answered runtime exited cleanly", pid is not None and not _alive(pid),
                       str(pid)))

        async def stopped_turn():
            session = _make_session(profile)
            try:
                turn = asyncio.create_task(session.run_turn(STOP_PROMPT))
                await _wait_for_question(session)
                started = time.monotonic()
                outcome = await session.stop()
                elapsed = time.monotonic() - started
                cancelled = None
                try:
                    await asyncio.wait_for(turn, 30)
                except TurnCancelled as exc:
                    cancelled = exc
                pending = len(session.pending_questions)
                pid = session.runtime._process.pid if session.runtime._process else None
                return outcome, elapsed, cancelled, pending, pid
            finally:
                await session.close()

        outcome, elapsed, cancelled, pending, pid = asyncio.run(stopped_turn())
        checks.append(("Stop confirmed while the question was waiting",
                       outcome.get("execution") == "stopped" and outcome.get("confirmed") is True,
                       json.dumps(outcome)))
        checks.append(("Stop returned promptly", elapsed < 10.0, f"{elapsed:.2f}s"))
        checks.append(("the waiting question was released", pending == 0, str(pending)))
        checks.append(("the cancelled turn never returned an answer",
                       cancelled is not None, "TurnCancelled" if cancelled else "returned normally"))
        checks.append(("no orphaned runtime processes",
                       pid is not None and not _alive(pid), str(pid)))
    finally:
        server.shutdown()

    summary = {"checks": [{"name": name, "passed": ok, "detail": detail}
                          for name, ok, detail in checks],
               "failed": [name for name, ok, _ in checks if not ok]}
    (work / "smoke_p07.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
