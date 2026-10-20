# This is the general benchmarking client
from __future__ import annotations

import argparse
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from odessy.voice.config import VoiceConfig

from .core import BenchmarkRunner
from .export import (
    print_lfm_comparison,
    print_summary,
    write_csv,
    write_jsonl,
    write_metadata,
)
from .fakes import FakeSTT, FakeTTS, NoOpAudioIO, fake_model
from .profiles import load_profiles
from .scenarios import (
    GemmaBenchmark,
    LFMCompareBenchmark,
    KokoroMatrixBenchmark,
    STTBenchmark,
    TTSBenchmark,
    VoiceTurnBenchmark,
    build_lfm_compare_model,
    build_fake_components,
    build_real_components,
    parse_lfm_variant,
)

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Odessy benchmark scenarios.")
    parser.add_argument(
        "--scenario",
        default="all",
        choices=["all", "stt", "gemma", "tts", "voice_turn", "lfm_compare"],
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
    parser.add_argument(
        "--manifest",
        default=None,
        help="TOML manifest for the component execution-path matrix.",
    )
    parser.add_argument(
        "--profile",
        action="append",
        default=None,
        help="Named matrix profile to run; may be supplied more than once.",
    )
    parser.add_argument(
        "--ollama-num-gpu",
        type=int,
        default=None,
        help="Benchmark-only Ollama num_gpu option for partial LFM GPU offload.",
    )
    parser.add_argument(
        "--tts-backend",
        choices=["onnx", "pytorch"],
        default="onnx",
        help="Real TTS backend for voice/tts scenarios.",
    )
    parser.add_argument(
        "--tts-device",
        choices=["auto", "cpu", "cuda"],
        default="auto",
        help="PyTorch TTS device; auto selects CUDA when available.",
    )
    parser.add_argument(
        "--lfm-variant",
        action="append",
        default=None,
        help="LFM comparison variant NAME=MODEL[@THINK_SETTING]; repeatable.",
    )
    parser.add_argument(
        "--expected-tool",
        default=None,
        help="Expected tool name for lfm_compare correctness scoring.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    repo_root = Path(__file__).resolve().parents[1]
    _maybe_reexec_with_jetson_cuda_path(
        args,
        repo_root=repo_root,
        process_entrypoint=argv is None,
    )
    workspace_root = Path(args.workspace_root) if args.workspace_root else repo_root
    output_dir = _resolve_output_dir(
        repo_root,
        args.output_dir,
        tts=args.manifest is not None or args.scenario == "tts",
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    config = VoiceConfig.from_env()
    if args.manifest:
        return _run_matrix(args, config, repo_root, workspace_root, output_dir)

    if args.seconds is not None:
        config = VoiceConfig(
            whisper_bin=config.whisper_bin,
            whisper_model=config.whisper_model,
            kokoro_model=config.kokoro_model,
            kokoro_voices=config.kokoro_voices,
            ollama_model=config.ollama_model,
            onnx_provider=config.onnx_provider,
            onnx_cache_dir=config.onnx_cache_dir,
            audio_recorder=config.audio_recorder,
            audio_player=config.audio_player,
            audio_input_device=config.audio_input_device,
            audio_output_device=config.audio_output_device,
            voice_seconds=max(1, args.seconds),
            tmp_dir=config.tmp_dir,
        )

    ollama_num_gpu = (
        args.ollama_num_gpu
        if args.ollama_num_gpu is not None
        else _optional_env_int("ODESSY_BENCHMARK_OLLAMA_NUM_GPU")
    )
    if args.scenario == "lfm_compare":
        scenarios = _build_lfm_compare_scenarios(
            args,
            ollama_num_gpu=ollama_num_gpu,
        )
    else:
        components = (
            build_fake_components()
            if args.adapter == "fake"
            else build_real_components(
                config,
                workspace_root,
                ollama_num_gpu=ollama_num_gpu,
                tts_backend=args.tts_backend,
                tts_device=args.tts_device,
            )
        )
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
            "ollama_num_gpu": ollama_num_gpu,
            "tts_backend": args.tts_backend,
            "tts_device": args.tts_device,
            "lfm_variants": args.lfm_variant,
            "expected_tool": args.expected_tool,
            # The model-call progress timeout materially changes what a run can
            # prove: with a 5 s budget, a completed 25 s generation is evidence
            # that the timeout is per-chunk rather than total. It was previously
            # recoverable only from the free-text command transcript, so a
            # machine-readable record could not distinguish a 5 s run from a
            # 120 s one.
            "model_progress_timeout_seconds": os.environ.get(
                "ODESSY_MODEL_PROGRESS_TIMEOUT", "120"
            ),
        },
    )
    run = runner.run(scenarios)

    write_metadata(output_dir / "metadata.json", run.metadata)
    write_csv(output_dir / "samples.csv", run.samples)
    write_jsonl(output_dir / "samples.jsonl", run.samples)
    print_summary(run)
    print_lfm_comparison(run)
    print(f"\nWrote benchmark data to: {output_dir}")
    return 0

def _run_matrix(
    args: argparse.Namespace,
    config: VoiceConfig,
    repo_root: Path,
    workspace_root: Path,
    output_dir: Path,
) -> int:
    if args.adapter != "real":
        raise SystemExit("--manifest requires --adapter real")
    manifest_path = Path(args.manifest)
    if not manifest_path.is_absolute():
        manifest_path = repo_root / manifest_path
    profiles = load_profiles(manifest_path)
    selected_names = args.profile or [
        name for name, profile in profiles.items() if profile.enabled
    ]
    unknown = [name for name in selected_names if name not in profiles]
    if unknown:
        raise SystemExit(f"Unknown benchmark profile(s): {', '.join(unknown)}")

    scenarios = []
    for name in selected_names:
        profile = profiles[name]
        if profile.component != "tts":
            raise SystemExit(
                f"Unsupported matrix component {profile.component!r} in profile {name!r}"
            )
        scenarios.append(
            KokoroMatrixBenchmark(
                profile=profile,
                text=args.text,
                output_dir=output_dir / "artifacts",
                config=config,
                warmup_iterations=max(0, args.warmup),
                measurement_iterations=max(0, args.runs),
            )
        )

    runner = BenchmarkRunner(
        runs=max(0, args.runs),
        warmup_runs=max(0, args.warmup),
        workspace_root=workspace_root,
        output_dir=output_dir,
        capture_memory=True,
        extra_metadata={
            "manifest": str(manifest_path),
            "profiles": selected_names,
            "scenario": "kokoro_execution_path_comparison",
            "adapter": args.adapter,
            "text": args.text,
        },
    )
    run = runner.run(scenarios)
    run.metadata.extra["profile_initialization_ms"] = {
        scenario.profile.name: scenario.initialization_ms
        for scenario in scenarios
    }
    write_metadata(output_dir / "metadata.json", run.metadata)
    write_csv(output_dir / "samples.csv", run.samples)
    write_jsonl(output_dir / "samples.jsonl", run.samples)
    print_summary(run)
    print(f"\nWrote benchmark data to: {output_dir}")
    return 0

def _resolve_output_dir(
    repo_root: Path,
    output_dir: str | None,
    *,
    tts: bool = False,
) -> Path:
    if output_dir:
        return Path(output_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    result_root = repo_root / "benchmark"
    if tts:
        result_root /= "tts"
    result_root /= "results"
    return result_root / stamp

def _optional_env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError:
        raise SystemExit(f"{name} must be an integer")


def _maybe_reexec_with_jetson_cuda_path(
    args: argparse.Namespace,
    *,
    repo_root: Path,
    process_entrypoint: bool,
) -> None:
    if not process_entrypoint or args.adapter != "real":
        return
    if os.environ.get("ODESSY_BENCHMARK_CUDA_ENV_READY") == "1":
        return
    if not _requests_pytorch_cuda(args, repo_root):
        return
    if platform.system() != "Linux":
        return

    from .tts.cuda_env import (
        build_jetson_native_cuda_library_path,
        jetson_native_cuda_path_is_complete,
    )

    if jetson_native_cuda_path_is_complete():
        return
    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = build_jetson_native_cuda_library_path()
    environment["ODESSY_BENCHMARK_CUDA_ENV_READY"] = "1"
    os.execvpe(
        sys.executable,
        [sys.executable, "-m", "benchmark", *sys.argv[1:]],
        environment,
    )


def _requests_pytorch_cuda(args: argparse.Namespace, repo_root: Path) -> bool:
    if args.manifest:
        manifest_path = Path(args.manifest)
        if not manifest_path.is_absolute():
            manifest_path = repo_root / manifest_path
        profiles = load_profiles(manifest_path)
        selected_names = args.profile or [
            name for name, profile in profiles.items() if profile.enabled
        ]
        return any(
            name in profiles
            and profiles[name].execution_path == "kokoro_pytorch"
            and profiles[name].pytorch_device == "cuda"
            for name in selected_names
        )
    return (
        args.tts_backend == "pytorch"
        and args.tts_device in {"auto", "cuda"}
        and args.scenario in {"tts", "voice_turn", "all"}
    )


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
                        input_audio_path=audio_path,
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
                        input_audio_path=audio_path,
                        seconds=config.voice_seconds,
                        preserve_artifacts=args.keep_artifacts,
                    )
                )
        else:
            raise SystemExit(f"Unknown scenario: {scenario_name}")

    return scenarios


def _build_lfm_compare_scenarios(
    args: argparse.Namespace,
    *,
    ollama_num_gpu: int | None,
):
    if args.adapter != "real":
        raise SystemExit("--scenario lfm_compare requires --adapter real")
    if not args.lfm_variant:
        raise SystemExit("--scenario lfm_compare requires at least one --lfm-variant")

    endpoint = os.environ.get("ODESSY_MODEL_ENDPOINT")
    scenarios = []
    for spec in args.lfm_variant:
        try:
            variant = parse_lfm_variant(spec)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        model = build_lfm_compare_model(
            variant,
            endpoint=endpoint,
            ollama_num_gpu=ollama_num_gpu,
        )
        scenarios.append(
            LFMCompareBenchmark(
                model=model,
                variant=variant,
                prompt=args.prompt,
                expected_tool=args.expected_tool,
            )
        )
    return scenarios

def _seed_fake_audio(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(b"FAKE-WAV")
    return path

def _synthetic_observation():
    from odessy.runtime.types import ToolObservation, ToolRequest

    return ToolObservation(
        request=ToolRequest(name="time.now", arguments={}),
        result={"iso_time": "2026-08-18T00:00:00+00:00"},
    )

if __name__ == "__main__":
    raise SystemExit(main())
