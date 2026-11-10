"""Per-session Stop against the pinned runtime (prompt 06).

Drives the production path with the real binary and the local deterministic provider:

    two runtimes -> both generating -> Stop one -> the other completes
        -> the stopped one runs another turn normally -> close both

and checks what actually happened: the runtime confirmed the cancellation, the cancelled
turn's text never became an answer, the unrelated session was untouched, the stopped
session stays usable, and no child is left behind.

    python smoke_p06.py --exe <pin> --home <DSH_HOME> --work <dir> [--toolchain <dir>]

The plugin is optional here: streaming text is prompt 05's evidence. Pass ``--toolchain`` when
the packaged bridge is not installed yet and you also want the delta checks.
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

from odessy.runtime_backends import StopOutcome, TurnCancelled  # noqa: E402
from odessy.runtime_backends.harness import HarnessRuntime  # noqa: E402
from odessy.runtime_backends.harness.config import from_environment  # noqa: E402
from odessy.runtime_backends.harness.routes import resolve_route  # noqa: E402

MODEL = "mock-model"
EXPECTED_TEXT = "Hello from the deterministic fixture."
LONG_PROMPT = "please slow cancel"      # 60 chunks, 250 ms apart: ~15 s of generation
STOP_AFTER = 1.5                         # seconds of genuine streaming before the Stop


class FakeController:
    """The slice of the application session the engine uses, with a thread-safe emitter."""

    def __init__(self, name, profile):
        self.id = name
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

    if args.toolchain:
        from smoke_p05 import install_plugin
        installed, detail = install_plugin(args.exe, home, Path(args.toolchain).resolve())
        if not installed:
            raise RuntimeError(detail)
    checks: list[tuple[str, bool, str]] = []
    server, port = mock_provider.serve(work / "mock")
    try:
        profile = {"name": "custom", "adapter": "cloud", "provider": "custom",
                   "model": MODEL, "endpoint": f"http://127.0.0.1:{port}/v1"}
        environ = base_environ(str(Path(args.exe).resolve()), home, workspace)
        environ["ODESSY_CLOUD_API_KEY"] = "mock-key-not-a-secret"
        route = resolve_route(profile, environ=environ)

        sessions = {}
        runtimes = {}
        for name in ("stopped-session", "unrelated-session"):
            sessions[name] = FakeController(name, profile)
            runtimes[name] = HarnessRuntime(
                sessions[name], config=from_environment(environ, workspace=workspace),
                route=route)
        stopped, unrelated = runtimes["stopped-session"], runtimes["unrelated-session"]
        seen = {"pids": {}, "generating": 0, "after_stop": None}

        def delta_count(name):
            return sum(1 for event in sessions[name].events if event["type"] == "delta")

        async def flow():
            try:
                await asyncio.gather(stopped.start(), unrelated.start())
                seen["pids"] = {"stopped": stopped._process.pid,
                                "unrelated": unrelated._process.pid}
                long_turn = asyncio.ensure_future(stopped.run_turn(LONG_PROMPT))
                other_turn = asyncio.ensure_future(unrelated.run_turn("hello"))
                await asyncio.sleep(STOP_AFTER)
                seen["generating"] = delta_count("stopped-session")
                seen["provider_started"] = (work / "mock" / "requests.jsonl").stat().st_size > 0
                started = time.monotonic()
                outcome = await stopped.stop(timeout=15.0)
                seen["stop_seconds"] = time.monotonic() - started
                cancelled = None
                try:
                    await long_turn
                except TurnCancelled as exc:
                    cancelled = exc
                other = await other_turn
                seen["after_stop"] = delta_count("stopped-session")
                await asyncio.sleep(1.5)     # nothing should arrive for a finished turn
                seen["later"] = delta_count("stopped-session")
                again = await stopped.run_turn("hello")
                seen["post_stop_pid"] = stopped._process.pid    # the session survived the Stop
                return outcome, cancelled, other, again
            finally:
                await stopped.close()
                await unrelated.close()

        outcome, cancelled, other, again = asyncio.run(flow())

        checks.append(("the pinned runtime confirms the cancellation",
                       isinstance(outcome, StopOutcome) and outcome.execution == "stopped"
                       and outcome.stop_reason == "cancelled" and outcome.confirmed,
                       json.dumps(outcome.public())))
        checks.append(("the Stop returned promptly",
                       seen.get("stop_seconds", 99) < 5.0,
                       f"{seen.get('stop_seconds', 0):.2f}s"))
        checks.append(("the turn was really generating when it was stopped",
                       (seen["generating"] > 0 if stopped.capabilities.live_text_deltas else seen["provider_started"]),
                       f"{seen['generating']} deltas before the Stop"))
        checks.append(("the cancelled text never became an answer",
                       cancelled is not None
                       and cancelled.outcome.get("stop_reason") == "cancelled",
                       "TurnCancelled" if cancelled else "the turn returned normally"))
        checks.append(("the unrelated session finished normally",
                       other.final_text == EXPECTED_TEXT,
                       other.final_text))
        checks.append(("the sessions are separate processes",
                       seen["pids"]["stopped"] != seen["pids"]["unrelated"],
                       str(sorted(seen["pids"].values()))))
        checks.append(("no text arrived after the turn was stopped",
                       seen["after_stop"] == seen["later"],
                       f"{seen['after_stop']} -> {seen['later']}"))
        checks.append(("the stopped session is still usable",
                       again.final_text == EXPECTED_TEXT, again.final_text))
        checks.append(("no orphaned runtime processes",
                       all(not _alive(pid) for pid in seen["pids"].values()),
                       str(sorted(seen["pids"].values()))))
        checks.append(("stops were not implemented by killing the child",
                       seen.get("post_stop_pid") == seen["pids"]["stopped"] and again.final_text == EXPECTED_TEXT,
                       "the stopped session ran another turn after the Stop"))
        requests = [json.loads(line) for line in
                    (work / "mock" / "requests.jsonl").read_text(
                        encoding="utf-8").splitlines() if line.strip()]
        checks.append(("the provider saw the cancelled request and the later ones",
                       len(requests) >= 3,
                       f"{len(requests)} requests"))
    finally:
        server.shutdown()

    summary = {"checks": [{"name": n, "passed": ok, "detail": d} for n, ok, d in checks],
               "failed": [n for n, ok, _ in checks if not ok]}
    (work / "smoke_p06.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if not summary["failed"] else 1


def _alive(pid):
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True, check=False).stdout
    return str(pid) in out


if __name__ == "__main__":
    raise SystemExit(main())
