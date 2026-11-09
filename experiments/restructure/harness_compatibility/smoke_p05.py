"""Live event translation against the pinned runtime (prompt 05).

Drives the production path with the real binary, the packaged bridge plugin and the
deterministic local provider:

    HarnessRuntime.start() -> run_turn("hello") -> run_turn("hello again") -> close()

and checks what actually crossed the boundary: that the plugin attached, that visible
text reached the session *before* the answer was committed, that the deltas join to
exactly the committed answer, that the loss counters stayed at zero, and that nothing was
left running. The plugin's own write-queue bound is measured separately, in isolation, by
``plugin_backpressure.mjs``.

    python smoke_p05.py --exe <pin> --home <DSH_HOME> --work <dir> [--toolchain <dir>]

The plugin must be installed in ``--home`` once (``install_plugin.py``); pass ``--toolchain``
to let this script do it when it is missing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import mock_provider  # noqa: E402

from odessy.runtime_backends.harness import HarnessRuntime  # noqa: E402
from odessy.runtime_backends.harness.config import from_environment  # noqa: E402
from odessy.runtime_backends.harness.routes import resolve_route  # noqa: E402

MODEL = "mock-model"
EXPECTED_TEXT = "Hello from the deterministic fixture."


class FakeController:
    """The slice of the application session the engine uses, with a thread-safe emitter."""

    def __init__(self, profile):
        self.id = "smoke-p05"
        self._events: list[dict] = []
        self._lock = threading.Lock()
        self._profile = profile
        self.model_profiles = [profile]

    @property
    def model_profile(self):
        return self._profile

    @property
    def events(self):
        with self._lock:
            return list(self._events)

    def _record(self, event_type, data):
        with self._lock:
            self._events.append({"id": len(self._events) + 1, "type": event_type,
                                 "time": time.time(), "data": data or {}})

    def emit(self, event_type, data=None):
        self._record(event_type, data)

    def emit_from_worker(self, event_type, data=None):
        self._record(event_type, data)

    def public_snapshot(self):
        return {"id": self.id, "events": len(self._events)}


PLATFORM = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
            "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA")


def base_environ(exe, home, workspace):
    environ = {name: os.environ[name] for name in PLATFORM if name in os.environ}
    environ.update({"ODESSY_HARNESS_EXE": exe, "ODESSY_HARNESS_HOME": str(home),
                    "ODESSY_HARNESS_WORKSPACE": str(workspace)})
    return environ


def plugin_installed(home: Path, profile: str = "acp") -> bool:
    manifest = home / "profiles" / profile / "package.json"
    if not manifest.exists():
        return False
    try:
        bundles = json.loads(manifest.read_text(encoding="utf-8"))
    except ValueError:
        return False
    listed = ((bundles.get("dsh") or {}).get("profile") or {}).get("bundles") or []
    return any("dsh-stream-bridge" in str(entry) for entry in listed)


def install_plugin(exe: str, home: Path, toolchain: Path, profile: str = "acp") -> tuple[bool, str]:
    command = [sys.executable, str(HERE / "install_plugin.py"), "--exe", exe,
               "--home", str(home), "--toolchain", str(toolchain), "--profile", profile]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    detail = (completed.stdout + completed.stderr).strip().splitlines()
    return completed.returncode == 0, (detail[-1] if detail else f"exit {completed.returncode}")


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
        installed = plugin_installed(home)
        detail = "already installed" if installed else "missing"
        if not installed and args.toolchain:
            installed, detail = install_plugin(args.exe, home, Path(args.toolchain).resolve())
        checks.append(("the bridge plugin is installed in the profile", installed, detail))

        profile = {"name": "custom", "adapter": "cloud", "provider": "custom",
                   "model": MODEL, "endpoint": f"http://127.0.0.1:{port}/v1"}
        environ = base_environ(str(Path(args.exe).resolve()), home, workspace)
        environ["ODESSY_CLOUD_API_KEY"] = "mock-key-not-a-secret"
        runtime = HarnessRuntime(FakeController(profile),
                                 config=from_environment(environ, workspace=workspace),
                                 route=resolve_route(profile, environ=environ))
        checks.append(("live text is not advertised before the bridge attaches",
                       runtime.capabilities.live_text_deltas is False,
                       str(runtime.capabilities)))

        turns = []
        seen = {"pid": 0, "capabilities": None}

        async def flow():
            try:
                await runtime.start()
                # Read while the engine is up: a closed engine advertises nothing, and
                # "what this session can do" is a claim about a running session.
                seen["pid"] = pid_of(runtime)
                seen["capabilities"] = runtime.capabilities
                return [await runtime.run_turn("hello"), await runtime.run_turn("hello again")]
            finally:
                await runtime.close()

        turns = asyncio.run(flow())
        deltas = [event for event in runtime._session.events if event["type"] == "delta"]

        checks.append(("the bridge attached at readiness",
                       any("live text: bridge attached (protocol 1" in line
                           for line in runtime.diagnostics()),
                       next((line for line in runtime.diagnostics()
                             if line.startswith("live text")), "no live-text line")))
        checks.append(("the session advertises live text",
                       getattr(seen["capabilities"], "live_text_deltas", None) is True,
                       str(seen["capabilities"])))

        # The two turns are separate translated streams.
        per_turn: dict[str, list[str]] = {}
        for event in deltas:
            per_turn.setdefault(event["data"]["stream"], []).append(event["data"]["text"])
        checks.append(("each turn has its own stream", len(per_turn) == 2,
                       ", ".join(sorted(per_turn))))
        checks.append(("at least three deltas were rendered per turn",
                       all(len(texts) >= 3 for texts in per_turn.values()) and len(per_turn) == 2,
                       str({key[:18]: len(value) for key, value in per_turn.items()})))
        checks.append(("the deltas join to exactly the committed answer",
                       turns[0].final_text == EXPECTED_TEXT
                       and any("".join(texts) == turns[0].final_text
                               for texts in per_turn.values())
                       and all("".join(texts) == EXPECTED_TEXT for texts in per_turn.values()),
                       json.dumps([turns[0].final_text, ["".join(v) for v in per_turn.values()]])))

        settlement = runtime.stream_settlement
        checks.append(("the first delta preceded the committed answer",
                       settlement.first_delta_ms is not None
                       and settlement.first_delta_ms < settlement.settled_ms - 300,
                       f"first delta at {settlement.first_delta_ms} ms of "
                       f"{settlement.settled_ms} ms"))
        checks.append(("nothing was lost on the happy path",
                       settlement.dropped == 0 and settlement.malformed == 0
                       and settlement.late == 0 and settlement.truncated is False
                       and settlement.foreign == 0,
                       json.dumps(settlement.public())))
        checks.append(("the settlement counts what was rendered",
                       settlement.deltas == len(per_turn.get(sorted(per_turn)[-1], [])),
                       f"settlement={settlement.deltas}"))

        requests = [json.loads(line)
                    for line in (work / "mock" / "requests.jsonl").read_text(
                        encoding="utf-8").splitlines() if line.strip()]
        checks.append(("both turns reached the provider", len(requests) >= 2,
                       f"{len(requests)} requests"))
        checks.append(("no orphaned runtime process",
                       seen["pid"] > 0 and not _alive(seen["pid"]),
                       str(seen["pid"])))
    finally:
        server.shutdown()

    summary = {"checks": [{"name": n, "passed": ok, "detail": d} for n, ok, d in checks],
               "failed": [n for n, ok, _ in checks if not ok]}
    (work / "smoke_p05.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if not summary["failed"] else 1


def pid_of(runtime) -> int:
    process = getattr(runtime, "_process", None)
    return int(getattr(process, "pid", 0) or 0)


def _alive(pid):
    if not pid:
        return False
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True, check=False).stdout
    return str(pid) in out


if __name__ == "__main__":
    raise SystemExit(main())
