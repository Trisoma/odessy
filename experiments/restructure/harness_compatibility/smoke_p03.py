"""Smoke: the packaged ``HarnessRuntime`` against the **pinned** runtime (prompt 03).

This is the one check that needs the downloaded artifact. It drives the production code
(`from_environment` -> `HarnessRuntime.start/run_turn/close`) exactly as a harness session
would, against the deterministic provider, and asserts:

- the executable reports the pinned version and is recorded as identity;
- the environment handed to the child is the allowlist (the base URL only arrives because
  it was named in ``ODESSY_HARNESS_PASS_ENV``);
- the reviewed overlay leaves the model with **no tools** (the provider's request log is
  empty of tools), so "no tools" is a property of the launched profile, not of a mock;
- one turn returns the provider's whole text without any streaming claim;
- close ends the child and leaves no process behind.

    python smoke_p03.py --exe <pinned exe> --home <DSH_HOME> --work <dir>
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import mock_provider  # noqa: E402

from odessy.runtime_backends.harness import (  # noqa: E402
    NARROW_PATCH_NAME,
    NARROW_PATCH_TEXT,
    HarnessRuntime,
)
from odessy.runtime_backends.harness.config import from_environment  # noqa: E402

EXPECTED_TEXT = "Hello from the deterministic fixture."


class FakeController:
    """The minimum surface a runtime may use, mirroring SessionController."""

    def __init__(self):
        self.id = "smoke-p03"
        self._events: list[dict] = []

    @property
    def events(self):
        return list(self._events)

    def emit(self, event_type, data=None):
        self._events.append({"id": len(self._events) + 1, "type": event_type,
                             "time": time.time(), "data": data or {}})

    def public_snapshot(self):
        return {"id": self.id, "events": len(self._events)}


def pid_alive(pid: int) -> bool:
    completed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                               capture_output=True, text=True, check=False)
    return str(pid) in completed.stdout


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

    route = work / "route.yml"
    route.write_text("- id: acp\n  config:\n    provider: deepseek-official\n"
                     "    model: mock-model\n", encoding="utf-8")
    server, port = mock_provider.serve(work / "mock")

    environ = {
        "ODESSY_HARNESS_EXE": str(Path(args.exe).resolve()),
        "ODESSY_HARNESS_HOME": str(home),
        "ODESSY_HARNESS_WORKSPACE": str(workspace),
        "ODESSY_HARNESS_PATCH": str(route),
        # The provider URL is *not* forwarded implicitly: it must be named here. The
        # credential is a literal mock value -- prompt 04 owns real credential handling,
        # and at this stage nothing is read from Odessy's credential store.
        "ODESSY_HARNESS_PASS_ENV": "DEEPSEEK_BASE_URL,DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{port}",
        "DEEPSEEK_API_KEY": "mock-key-not-a-secret",
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
        "USERPROFILE": os.environ.get("USERPROFILE", ""),
        "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
        "APPDATA": os.environ.get("APPDATA", ""),
        "PATHEXT": os.environ.get("PATHEXT", ""),
        "COMSPEC": os.environ.get("COMSPEC", ""),
    }

    # Prove the allowlist is an allowlist: a variable that exists in this process but is
    # not named in ODESSY_HARNESS_PASS_ENV must not reach the child.
    os.environ["ODESSY_SMOKE_SECRET"] = "must-not-reach-the-child"
    config = from_environment(environ, workspace=workspace)
    child_env = config.child_env()

    checks: list[tuple[str, bool, str]] = []
    checks.append(("unnamed parent variable excluded",
                   "ODESSY_SMOKE_SECRET" not in child_env, f"{len(child_env)} names"))
    checks.append(("named passthrough arrived",
                   str(child_env.get("DEEPSEEK_BASE_URL", "")).startswith("http://127.0.0.1:"),
                   str(child_env.get("DEEPSEEK_BASE_URL", ""))))
    checks.append(("child home is the isolated one", child_env.get("DSH_HOME") == str(home),
                   str(child_env.get("DSH_HOME"))))
    narrow = config.ordered_patches()[-1]
    checks.append(("narrow overlay is applied last", narrow.name == NARROW_PATCH_NAME,
                   str(narrow)))
    checks.append(("launched overlay is the reviewed text",
                   narrow.read_text(encoding="utf-8") == NARROW_PATCH_TEXT,
                   f"{len(NARROW_PATCH_TEXT)} chars"))
    checks.append(("narrow overlay lives in the owned home",
                   narrow.parent == home, str(narrow.parent)))

    controller = FakeController()
    runtime = HarnessRuntime(controller, config=config)
    pid = None
    try:
        async def flow():
            nonlocal pid
            try:
                identity = await runtime.start()
                pid = runtime._process.pid
                result = await runtime.run_turn("hello")
                return identity, result
            finally:
                await runtime.close()

        identity, result = asyncio.run(flow())

        checks.append(("pinned version recorded", identity.get("version") == "0.2.1-rc.2",
                       str(identity.get("version"))))
        checks.append(("executable artifact", identity.get("artifact") == "pinned",
                       str(identity.get("artifact"))))
        checks.append(("turn text equals the provider text", result.final_text == EXPECTED_TEXT,
                       result.final_text))
        checks.append(("no streaming claimed", runtime.capabilities.live_text_deltas is False,
                       "live_text_deltas=False"))

        log = work / "mock" / "requests.jsonl"
        requests = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        roster = (requests[-1].get("tools") or []) if requests else None
        checks.append(("provider was called", bool(requests), f"{len(requests)} requests"))
        checks.append(("no tool reached the model", roster == [], str(roster)))

        diagnostics = runtime.diagnostics()
        ready = next((line for line in diagnostics if line.startswith("ready:")), "")
        checks.append(("readiness records version, artifact and negotiated protocol",
                       "version=0.2.1-rc.2" in ready and "artifact=pinned" in ready
                       and "protocolVersion=1" in ready, ready or "no readiness line"))
        owned = any("process tree owned by a job object" in line for line in diagnostics)
        fallback = any("no job object" in line for line in diagnostics)
        checks.append(("descendants are owned by a job object", owned and not fallback,
                       "owned" if owned and not fallback else "FALLBACK: " + str(diagnostics[:2])))
        checks.append(("graceful shutdown ran", any("closing stdin" in line
                                                    for line in diagnostics),
                       next((line for line in diagnostics if "closing stdin" in line), "n/a")))
        checks.append(("child exited", runtime._process.exit_code is not None,
                       str(runtime._process.exit_code)))
    finally:
        server.shutdown()
    time.sleep(0.2)
    checks.append(("no orphaned runtime process", pid is None or not pid_alive(pid), str(pid)))

    summary = {
        "version": "0.2.1-rc.2",
        "checks": [{"name": name, "passed": ok, "detail": detail} for name, ok, detail in checks],
        "failed": [name for name, ok, _ in checks if not ok],
    }
    (work / "smoke_p03.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
