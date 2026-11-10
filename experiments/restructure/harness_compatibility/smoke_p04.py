"""End-to-end model routing against the pinned runtime (prompt 04).

Drives the production path with the real binary and the local deterministic endpoint:

    resolve_route(profile) -> HarnessRuntime(..., route=...) -> start -> turn
        -> select_route(other model) -> turn -> close

and checks what the endpoint actually received: the declared route, the model ids the
runtime advertised, one credential-bearing request per turn, and that a missing key is
refused before anything is spawned.

    python smoke_p04.py --exe <pin> --home <DSH_HOME> --work <dir>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import mock_provider  # noqa: E402

from odessy.runtime_backends import ModelRouteError, RuntimeUnavailable  # noqa: E402
from odessy.runtime_backends.harness import HarnessRuntime  # noqa: E402
from odessy.runtime_backends.harness.config import from_environment  # noqa: E402
from odessy.runtime_backends.harness.routes import (  # noqa: E402
    materialize_route_patch,
    resolve_route,
)

EXPECTED_TEXT = "Hello from the deterministic fixture."
FIRST, SECOND = "mock-model", "mock-model-2"


class FakeController:
    def __init__(self, profile):
        self.id = "smoke-p04"
        self._events: list[dict] = []
        self._profile = profile
        self.model_profiles = [profile, {**profile, "name": "second", "model": SECOND}]

    @property
    def model_profile(self):
        return self._profile

    @property
    def events(self):
        return list(self._events)

    def emit(self, event_type, data=None):
        self._events.append({"id": len(self._events) + 1, "type": event_type,
                             "time": time.time(), "data": data or {}})

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
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    workspace = work / "ws"
    for path in (work / "mock", workspace, home):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")

    server, port = mock_provider.serve(work / "mock")
    profile = {"name": "custom", "adapter": "cloud", "provider": "custom",
               "model": FIRST, "endpoint": f"http://127.0.0.1:{port}/v1"}
    checks: list[tuple[str, bool, str]] = []
    try:
        # --- a missing key is refused before anything is spawned -----------
        try:
            resolve_route(profile, environ={})
            refused = False
            detail = "no error"
        except RuntimeUnavailable as exc:
            refused = True
            detail = exc.public()["detail"][:120]
        checks.append(("a missing key is refused before launch", refused, detail))

        environ = base_environ(str(Path(args.exe).resolve()), home, workspace)
        environ["ODESSY_CLOUD_API_KEY"] = "mock-key-not-a-secret"
        route = resolve_route(profile, environ=environ)
        checks.append(("route declared", route.route == "odessy-custom",
                       f"{route.route} -> {route.base_url}"))

        # Two routes in one home must not share an overlay file.
        other = resolve_route({**profile, "endpoint": f"http://127.0.0.1:{port}/other/v1"},
                              environ=environ)
        first_patch = materialize_route_patch(home, route)
        second_patch = materialize_route_patch(home, other)
        checks.append(("one overlay file per route",
                       first_patch != second_patch
                       and first_patch.read_text(encoding="utf-8") == route.overlay_text()
                       and other.base_url in second_patch.read_text(encoding="utf-8"),
                       f"{first_patch.name} / {second_patch.name}"))
        checks.append(("credential is referenced, not embedded",
                       "mock-key-not-a-secret" not in route.overlay_text(),
                       route.public()["credential"]))

        runtime = HarnessRuntime(FakeController(profile),
                                 config=from_environment(environ, workspace=workspace),
                                 route=route)

        async def flow():
            try:
                await runtime.start()
                first = await runtime.run_turn("hello")
                second = await runtime.run_turn("hello again")
                return first, second
            finally:
                await runtime.close()

        first, second = asyncio.run(flow())
        checks.append(("turn text equals the endpoint's answer",
                       first.final_text == EXPECTED_TEXT and second.final_text == EXPECTED_TEXT,
                       first.final_text))
        checks.append(("readiness selected the declared pair",
                       any("route=odessy-custom model=mock-model" in line
                           for line in runtime.diagnostics()),
                       next((line for line in runtime.diagnostics()
                             if line.startswith("ready:")), "no readiness line")))
        checks.append(("context usage recorded, no tokens invented",
                       runtime.context_usage.get("size", 0) > 0
                       and first.metrics.prompt_tokens is None,
                       json.dumps(runtime.context_usage)))

        log = work / "mock" / "requests.jsonl"
        requests = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        checks.append(("both turns reached the endpoint", len(requests) == 2,
                       f"{len(requests)} requests"))
        checks.append(("the endpoint saw the declared model", [r.get("model") for r in requests]
                       == [FIRST, FIRST], str([r.get("model") for r in requests])))
        checks.append(("provider path is the declared openai-compatible one",
                       all(r.get("path") == "/v1/chat/completions" for r in requests),
                       str(sorted({r.get("path") for r in requests}))))

        # --- a model change on the same route, no relaunch ----------------
        pid_before = runtime._process.pid
        switch_route = resolve_route(profile, environ=environ, extra_models=(SECOND,))
        switch = HarnessRuntime(FakeController(profile),
                                config=from_environment(environ, workspace=workspace),
                                route=switch_route)

        async def switching():
            try:
                await switch.start()
                before = switch._process.pid
                await switch.run_turn("hello")
                applied = switch.select_route({**profile, "name": "second", "model": SECOND},
                                              profile_name="second")
                after = switch._process.pid
                await switch.run_turn("hello again")
                # A launch-fixed setting may not be recorded without being applied.
                refused = None
                try:
                    switch.select_route(
                        {**profile, "name": "moved",
                         "endpoint": f"http://127.0.0.1:{port}/moved/v1"},
                        profile_name="moved")
                except ModelRouteError as exc:
                    refused = str(exc)
                return before, after, applied, refused, switch.route.base_url
            finally:
                await switch.close()

        before, after, applied, refused, base_url_after = asyncio.run(switching())
        checks.append(("a model change keeps the same process", before == after, f"{before}->{after}"))
        checks.append(("an endpoint change is refused, not recorded",
                       refused is not None and "base_url" in refused
                       and base_url_after == profile["endpoint"],
                       (refused or "no error")[:100]))
        checks.append(("the change reports the new model", applied["model"] == SECOND,
                       applied["model"]))
        requests = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
                    if line.strip()]
        checks.append(("the endpoint saw the switched model",
                       [r.get("model") for r in requests][-2:] == [FIRST, SECOND],
                       str([r.get("model") for r in requests])))
        checks.append(("no orphaned runtime process", not _alive(pid_before), str(pid_before)))
    finally:
        server.shutdown()

    summary = {"checks": [{"name": n, "passed": ok, "detail": d} for n, ok, d in checks],
               "failed": [n for n, ok, _ in checks if not ok]}
    (work / "smoke_p04.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if not summary["failed"] else 1


def _alive(pid):
    import subprocess
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True, check=False).stdout
    return str(pid) in out


if __name__ == "__main__":
    raise SystemExit(main())
