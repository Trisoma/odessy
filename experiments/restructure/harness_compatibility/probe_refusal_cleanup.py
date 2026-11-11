"""Does a *refused* restore leave its runtime child alive?

The prompt-10 smoke observed one runtime process per refused restore surviving the refusal (and
dying only when the whole test process exited). A refusal is supposed to discard the child it
started, so this isolates the path: force the tool-roster refusal with
``ODESSY_HARNESS_TOOL_ROSTER=required`` and no plugin, then watch the pid.

    python probe_refusal_cleanup.py --exe <pinned exe> --home <DSH_HOME> --work <dir>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "src"))

from odessy.application.session import SessionController  # noqa: E402
from odessy.runtime_backends import RuntimeUnavailable  # noqa: E402


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        completed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                   capture_output=True, text=True, check=False)
        return str(pid) in completed.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _session(profile: dict) -> SessionController:
    return SessionController(
        model_factory=lambda name: object(),
        agent_factory=lambda *args, **kwargs: None,
        profiles={"custom": dict(profile)},
        selected_profile="custom",
        runtime_selection="harness",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    workspace = work / "ws"
    for path in (workspace, home):
        path.mkdir(parents=True, exist_ok=True)

    findings: dict = {}
    os.environ.update({
        "ODESSY_HARNESS_EXE": str(Path(args.exe).resolve()),
        "ODESSY_HARNESS_HOME": str(home),
        "ODESSY_HARNESS_WORKSPACE": str(workspace),
        "ODESSY_CLOUD_API_KEY": "mock-key-not-a-secret",
        "ODESSY_HARNESS_TOOLS": "on",
        "ODESSY_HARNESS_TOOL_ROSTER": "required",
    })
    profile = {"name": "custom", "adapter": "cloud", "provider": "custom",
               "model": "mock-model", "endpoint": "http://127.0.0.1:9/v1"}

    async def run() -> None:
        session = _session(profile)
        runtime = session.runtime
        try:
            await asyncio.wait_for(session.prepare(), 180)
            findings["refused"] = False
        except RuntimeUnavailable as exc:
            findings["refused"] = True
            findings["reason"] = exc.public()["reason"]
        process = runtime._process
        findings["published_process"] = process is not None
        child = getattr(process, "_process", None) if process is not None else None
        pid = getattr(child, "pid", None)
        findings["pid"] = pid
        findings["diagnostics"] = runtime.diagnostics()[-6:]
        if pid:
            findings["alive_right_after"] = _alive(pid)
            for _ in range(20):
                if not _alive(pid):
                    break
                time.sleep(0.5)
            findings["alive_after_10s"] = _alive(pid)
        # Also try the explicit close path, to see whether it is the refusal or the teardown.
        try:
            await asyncio.wait_for(session.close(), 60)
            findings["close_ok"] = True
        except BaseException as exc:  # noqa: BLE001
            findings["close_ok"] = f"{type(exc).__name__}: {exc}"
        if pid:
            findings["alive_after_close"] = _alive(pid)

    asyncio.run(run())
    print(json.dumps(findings, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
