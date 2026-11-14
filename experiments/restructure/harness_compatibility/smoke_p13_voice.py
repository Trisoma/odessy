"""Prompt 13 smoke: real pinned-runtime text drives the voice speech path.

This drives the *application* session boundary (``create_controller``, the same object
the HTTP server serves) against the pinned runtime and its deterministic provider
fixture. It proves the two things a voice turn actually depends on and that no unit
test can prove alone:

1. the pinned engine's transient text reaches Odessy's existing sentence segmenter, so
   the reply is spoken while the turn is still running, and the committed answer is not
   spoken a second time;
2. a Stop during that stream ends the turn through prompt 06's cancellation, and nothing
   from the cancelled reply is spoken afterwards.

Provider latency is the fixture's, not the bridge's: ``first_text_ms`` in the phase
timing covers model and network time and is reported separately from the runtime's own
``first_delta_ms`` settlement figure.

Usage (run from the repository root, developer machine only):
    python experiments/restructure/harness_compatibility/smoke_p13_voice.py \
        --exe <pinned runtime exe> --home <fresh dsh home> --work <work dir> \
        --toolchain <node/pnpm toolchain dir>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mock_provider  # noqa: E402

from odessy.application.bootstrap import create_controller  # noqa: E402
from odessy.application.durable import SessionStore  # noqa: E402
from odessy.collaboration.config import load_config  # noqa: E402
from odessy.runtime.speech import SpeechStream, speech_scope  # noqa: E402
from odessy.voice.timing import DELTA_SOURCE  # noqa: E402
from odessy.voice.timing import VoiceTiming  # noqa: E402

MODEL = "mock-model"
PROFILE = "custom"


def _profile(port: int) -> dict:
    return {"name": PROFILE, "adapter": "cloud", "provider": "custom",
            "model": MODEL, "endpoint": f"http://127.0.0.1:{port}/v1"}


def _install_bundle(exe: str, home: Path, toolchain: Path) -> tuple[bool, str]:
    import subprocess

    completed = subprocess.run(
        [sys.executable, str(HERE / "install_plugin.py"), "--exe", exe,
         "--home", str(home), "--toolchain", str(toolchain)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    detail = (completed.stdout or completed.stderr or "").strip().splitlines()[-1:] or [""]
    return completed.returncode == 0, detail[0][:200]


async def _wait_until(predicate, timeout: float, what: str) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--toolchain", required=True)
    args = parser.parse_args()

    work = Path(args.work).resolve()
    home = Path(args.home).resolve()
    workspace = work / "ws"
    state = work / "state"
    for path in (work / "mock", workspace, home, state):
        path.mkdir(parents=True, exist_ok=True)
    (work / "mock" / "requests.jsonl").write_text("", encoding="utf-8")

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, passed: bool, detail: str = "") -> None:
        checks.append((name, bool(passed), detail))

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    server, port = mock_provider.serve(work / "mock")
    try:
        os.environ.update({
            "ODESSY_HARNESS_HOME": str(home),
            "ODESSY_HARNESS_WORKSPACE": str(workspace),
            "ODESSY_HARNESS_EXE": str(Path(args.exe).resolve()),
            "ODESSY_HARNESS_TOOL_PYTHON": sys.executable,
            "ODESSY_CLOUD_API_KEY": "mock-key-not-a-secret",
            "ODESSY_SESSION_STATE_DIR": str(state),
        })
        installed, install_detail = _install_bundle(
            str(Path(args.exe).resolve()), home, Path(args.toolchain).resolve())
        check("the packaged stream bridge is installed for this run", installed,
              install_detail)

        from odessy.collaboration.config import (
            CollaborationConfig, CollaborationSettings, ParticipantProfile, save_config,
        )

        profile = _profile(port)
        collaboration_file = work / "collaboration.toml"
        save_config(CollaborationConfig(
            CollaborationSettings(),
            {profile["name"]: ParticipantProfile(
                profile["name"], "cloud", provider=profile["provider"],
                model=profile["model"], endpoint=profile["endpoint"])}),
            collaboration_file)
        os.environ["ODESSY_COLLABORATION_FILE"] = str(collaboration_file)

        async def scenario() -> None:
            controller = await create_controller(
                workspace=workspace, use_fake_model=True, selected_profile=PROFILE,
                durable=SessionStore(state), runtime_selection="harness")
            try:
                check("the session runs on the selected engine", controller.engine == "harness",
                      controller.engine)

                # -- streaming text reaches the voice segmenter, once ------------------
                timing = VoiceTiming().begin("p13-turn-1")
                controller.bind_turn_timing(timing)
                spoken: list[str] = []
                stream = SpeechStream(spoken.append)
                with speech_scope(stream):
                    result = await controller.run_turn("ping the prompt thirteen voice path")
                controller.bind_turn_timing(None)
                events = [event["type"] for event in controller.events]
                check("the pinned engine produced live deltas", "delta" in events,
                      str(sorted(set(events)))[:200])
                check("the reply was spoken sentence by sentence while the turn ran",
                      bool(spoken), json.dumps(spoken)[:200])
                check("the committed answer was not said a second time",
                      not stream.unspoken(result.final_text),
                      json.dumps({"spoken": stream.spoken,
                                  "answer": result.final_text})[:240])
                phase = timing.public()
                check("the first text is timed with its source",
                      phase["first_text_source"] == DELTA_SOURCE
                      and phase["first_text_ms"] is not None, json.dumps(phase)[:200])
                check("recognition and playback are reported as not measured, not as zero",
                      "stt" in phase["not_measured"] and "first_audio" in phase["not_measured"],
                      json.dumps(phase["not_measured"]))

                # -- Stop during the stream: the rest is never spoken -------------------
                before = len(spoken)
                stop_stream = SpeechStream(spoken.append)
                outcome: dict = {}
                turn = None
                with speech_scope(stop_stream):
                    turn = asyncio.create_task(controller.run_turn("slow sentences please"))
                    # Wait until at least one sentence of this turn has been spoken.
                    await _wait_until(lambda: len(spoken) > before, 20.0, "a spoken sentence")
                    outcome = await controller.stop()
                    spoken_at_stop = list(spoken)
                    try:
                        await asyncio.wait_for(turn, timeout=30)
                    except asyncio.CancelledError:  # local cancellation is an expected Stop outcome
                        pass
                provider_finished = await _wait_until(
                    lambda: server.stream_completions.get("slow sentences please") is not None
                    and server.stream_completions["slow sentences please"].is_set(),
                    20.0, "provider stream completion")
                check("the stopped provider stream finished within the bound", provider_finished)
                check("Stop answers what the pinned runtime established",
                      outcome.get("execution") in {"stopped", "unconfirmed"},
                      json.dumps(outcome)[:240])
                check("the stop is visible in the event stream",
                      "stopped" in [event["type"] for event in controller.events],
                      str(sorted(set(event["type"] for event in controller.events)))[:200])
                check("sentences spoken before the Stop stay spoken",
                      len(spoken_at_stop) > before, json.dumps(spoken_at_stop[before:])[:200])
                check("nothing from the cancelled reply is spoken afterwards",
                      provider_finished and spoken == spoken_at_stop, json.dumps(spoken[len(spoken_at_stop):])[:200])
                check("the cancelled reply's unfinished text is not spoken",
                      all("tail" not in sentence for sentence in spoken),
                      json.dumps([s for s in spoken if "tail" in s])[:200])

            finally:
                await controller.close()

        asyncio.run(scenario())
    finally:
        server.shutdown()

    passed = sum(1 for _, ok, _ in checks if ok)
    print(json.dumps({"checks": [{"check": name, "passed": ok, "detail": detail}
                                 for name, ok, detail in checks],
                      "summary": f"{passed}/{len(checks)} checks passed"},
                     indent=2, ensure_ascii=False))
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
