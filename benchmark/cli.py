from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from odessy.voice.config import VoiceConfig

from .core import BenchmarkRunner
from .export import print_summary, write_csv, write_jsonl, write_metadata
from .fakes import FakeSTT, FakeTTS, NoOpAudioIO, fake_model
from .scenarios import (
    GemmaBenchmark,
    STTBenchmark,
    TTSBenchmark,
    VoiceTurnBenchmark,
    build_fake_components,
    build_real_components,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Odessy benchmark scenarios.")
    parser.add_argument(
        "--scenario",
        default="all",
        choices=["all", "stt", "gemma", "tts", "voice_turn"],
        help="Benchmark scenario to run.",
    )
    parser.add_argument("--runs", type=int, default=5, help="Measured runs per scenario.")
    parser.add_argument("--warmup", type=int, default=1, help="Warmup runs per scenario.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for CSV, JSONL, and metadata.",
    )
    parser.add_argument(
        "--adapter",
        choices=["fake", "real"],
        default="fake",
        help="Use fake adapters for hardware-free benchmarking or real Jetson components.",
    )
    parser.add_argument(
        "--audio-path",
        default=None,
        help="Input WAV file for STT and voice-turn benchmarks.",
    )
    parser.add_argument(
        "--prompt",
        default="Who are you? Briefly introduce yourself.",
        help="Prompt used for the Gemma benchmark and as the voice transcript in fake tests.",
    )
    parser.add_argument(
        "--text",
        default="Odessy is a local AI assistant.",
        help="Text used for the TTS benchmark.",
    )
    parser.add_argument(
        "--seconds",
        type=int,
        default=None,
        help="Recording duration in seconds for voice-turn benchmarks.",
    )
    parser.add_argument(
        "--workspace-root",
        default=None,
        help="Workspace root used for policy metadata and the real voice agent.",
    )
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep generated audio artifacts instead of cleaning them up after each run.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    repo_root = Path(__file__).resolve().parents[1]
    workspace_root = Path(args.workspace_root) if args.workspace_root else repo_root
    output_dir = _resolve_output_dir(repo_root, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = VoiceConfig.from_env()
    if args.seconds is not None:
        config = VoiceConfig(
            whisper_bin=config.whisper_bin,
            whisper_model=config.whisper_model,
            kokoro_model=config.kokoro_model,
            kokoro_voices=config.kokoro_voices,
            ollama_model=config.ollama_model,
            audio_recorder=config.audio_recorder,
            audio_player=config.audio_player,
            audio_input_device=config.audio_input_device,
            audio_output_device=config.audio_output_device,
            voice_seconds=max(1, args.seconds),
            tmp_dir=config.tmp_dir,
        )

    components = build_fake_components() if args.adapter == "fake" else build_real_components(config, workspace_root)
    scenarios = _build_scenarios(args, config, output_dir, components)

    runner = BenchmarkRunner(
        runs=max(0, args.runs),
        warmup_runs=max(0, args.warmup),
        workspace_root=workspace_root,
        output_dir=output_dir,
        capture_memory=True,
        extra_metadata={
            "scenario": args.scenario,
            "adapter": args.adapter,
            "audio_path": args.audio_path,
            "prompt": args.prompt,
            "text": args.text,
        },
    )
    run = runner.run(scenarios)

    write_metadata(output_dir / "metadata.json", run.metadata)
    write_csv(output_dir / "samples.csv", run.samples)
    write_jsonl(output_dir / "samples.jsonl", run.samples)
    print_summary(run)
    print(f"\nWrote benchmark data to: {output_dir}")
    return 0


def _resolve_output_dir(repo_root: Path, output_dir: str | None) -> Path:
    if output_dir:
        return Path(output_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return repo_root / "benchmark" / "results" / stamp


def _build_scenarios(
    args: argparse.Namespace,
    config: VoiceConfig,
    output_dir: Path,
    components: dict[str, object],
):
    selected = [args.scenario] if args.scenario != "all" else ["stt", "gemma", "tts", "voice_turn"]
    scenarios = []
    audio_path = Path(args.audio_path) if args.audio_path else None

    for scenario_name in selected:
        if scenario_name == "stt":
            if args.adapter == "fake":
                stt = components["stt"]
                if audio_path is None:
                    audio_path = _seed_fake_audio(output_dir / "fake-input.wav")
            else:
                if audio_path is None:
                    raise SystemExit("--audio-path is required for a real STT benchmark")
                stt = components["stt"]
            scenarios.append(STTBenchmark(stt=stt, audio_path=audio_path))
        elif scenario_name == "gemma":
            model = components["model"]
            scenarios.append(
                GemmaBenchmark(
                    model=model,
                    prompt=args.prompt,
                    synthetic_observation=_synthetic_observation(),
                )
            )
        elif scenario_name == "tts":
            tts = components["tts"]
            scenarios.append(TTSBenchmark(tts=tts, text=args.text, output_dir=output_dir / "artifacts"))
        elif scenario_name == "voice_turn":
            if args.adapter == "fake":
                agent = components["agent"]
                stt = components["stt"]
                tts = components["tts"]
                audio_io = components["audio_io"]
                scenarios.append(
                    VoiceTurnBenchmark(
                        agent=agent,
                        audio_io=audio_io,  # type: ignore[arg-type]
                        stt=stt,
                        tts=tts,
                        tmp_dir=output_dir / "artifacts",
                        seconds=config.voice_seconds,
                        preserve_artifacts=args.keep_artifacts,
                    )
                )
            else:
                if audio_path is None:
                    raise SystemExit("--audio-path is required for a real voice-turn benchmark")
                agent = components["agent"]
                stt = components["stt"]
                tts = components["tts"]
                audio_io = components["audio_io"]
                scenarios.append(
                    VoiceTurnBenchmark(
                        agent=agent,
                        audio_io=audio_io,  # type: ignore[arg-type]
                        stt=stt,
                        tts=tts,
                        tmp_dir=output_dir / "artifacts",
                        seconds=config.voice_seconds,
                        preserve_artifacts=args.keep_artifacts,
                    )
                )
        else:
            raise SystemExit(f"Unknown scenario: {scenario_name}")

    return scenarios


def _seed_fake_audio(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(b"FAKE-WAV")
    return path


def _synthetic_observation():
    from odessy.types import ToolObservation, ToolRequest

    return ToolObservation(
        request=ToolRequest(name="time.now", arguments={}),
        result={"iso_time": "2026-08-18T00:00:00+00:00"},
    )


if __name__ == "__main__":
    raise SystemExit(main())
