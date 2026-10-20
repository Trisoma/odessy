from __future__ import annotations

import csv
import json
import asyncio
from pathlib import Path

from benchmark.cli import main as benchmark_main
from benchmark.core import BenchmarkOutcome, BenchmarkRunner
from benchmark.export import write_csv, write_jsonl, write_metadata
from benchmark.fakes import FakeSTT, FakeTTS, NoOpAudioIO, fake_model
from benchmark.scenarios import (
    BenchmarkPyTorchTTS,
    GemmaBenchmark,
    LFMCompareBenchmark,
    LFMVariant,
    STTBenchmark,
    TTSBenchmark,
    VoiceTurnBenchmark,
    build_real_components,
    parse_lfm_variant,
)
from odessy.agent_loop import OdessyAgent
from odessy.policy import OdessyPolicy
from odessy.types import (
    FinalResponse,
    ModelTurnDiagnostics,
    ToolObservation,
    ToolRequest,
)
from odessy.voice.config import VoiceConfig


def test_runner_writes_csv_and_jsonl(tmp_path: Path) -> None:
    model = fake_model()
    policy = OdessyPolicy(workspace_root=tmp_path)
    agent = OdessyAgent(model=model, policy=policy, mcp_server=None)
    scenarios = [
        GemmaBenchmark(
            model=model,
            prompt="Who are you?",
            synthetic_observation=ToolObservation(
                request=ToolRequest(name="time.now", arguments={}),
                result={"iso_time": "2026-08-18T00:00:00+00:00"},
            ),
        ),
        STTBenchmark(stt=FakeSTT(), audio_path=_fake_audio(tmp_path)),
        TTSBenchmark(tts=FakeTTS(), text="Hello", output_dir=tmp_path / "artifacts"),
        VoiceTurnBenchmark(
            agent=agent,
            audio_io=NoOpAudioIO(),
            stt=FakeSTT(),
            tts=FakeTTS(),
            tmp_dir=tmp_path / "artifacts",
            input_audio_path=_fake_audio(tmp_path),
            seconds=1,
            preserve_artifacts=False,
        ),
    ]

    runner = BenchmarkRunner(
        runs=2,
        warmup_runs=1,
        workspace_root=tmp_path,
        output_dir=tmp_path / "out",
        capture_memory=True,
    )
    run = runner.run(scenarios)

    write_metadata(tmp_path / "out" / "metadata.json", run.metadata)
    write_csv(tmp_path / "out" / "samples.csv", run.samples)
    write_jsonl(tmp_path / "out" / "samples.jsonl", run.samples)

    assert (tmp_path / "out" / "metadata.json").exists()
    assert (tmp_path / "out" / "samples.csv").exists()
    assert (tmp_path / "out" / "samples.jsonl").exists()

    with (tmp_path / "out" / "samples.csv").open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {"scenario", "iteration", "total_ms", "status"}.issubset(rows[0].keys())
    assert "benchmark_process_rss_before_bytes" in rows[0]
    assert "process_tree_rss_after_bytes" in rows[0]
    assert "system_ram_used_after_bytes" in rows[0]
    assert "components_invoked" in rows[0]
    voice_rows = [row for row in rows if row["scenario"] == "voice_turn"]
    assert voice_rows
    assert voice_rows[0]["agent_turn_total_ms"]
    assert voice_rows[0]["proposal_generation_ms"]
    assert voice_rows[0]["tool_called"] in {"True", "False"}
    assert "model_total_duration_ms" in voice_rows[0]
    assert "generation_tokens_per_sec" in voice_rows[0]
    assert "tool_selection_correct" in voice_rows[0]

    with (tmp_path / "out" / "samples.jsonl").open("r", encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    assert len(lines) == len(run.samples)
    voice_items = [item for item in lines if item["scenario"] == "voice_turn"]
    assert voice_items[0]["agent_turn_total_ms"] is not None


def test_voice_turn_uses_provided_audio_without_recording_copy(tmp_path: Path) -> None:
    source_audio = _fake_audio(tmp_path)
    audio_io = GuardedAudioIO()
    agent = OdessyAgent(
        model=fake_model(),
        policy=OdessyPolicy(workspace_root=tmp_path),
        mcp_server=None,
    )
    scenario = VoiceTurnBenchmark(
        agent=agent,
        audio_io=audio_io,
        stt=FakeSTT("hello"),
        tts=FakeTTS(),
        tmp_dir=tmp_path / "artifacts",
        input_audio_path=source_audio,
        seconds=1,
        preserve_artifacts=False,
    )

    outcome = scenario.run_once(iteration=0, is_warmup=False)

    assert outcome.status == "ok"
    assert outcome.details["input_audio_path"] == str(source_audio)
    assert source_audio.exists()
    assert audio_io.record_called is False


def test_summary_excludes_warmups(tmp_path: Path) -> None:
    scenario = ScriptedScenario(
        "scripted",
        [BenchmarkOutcome(status="ok", stage_ms={"proposal_ms": 10.0}, details={}) for _ in range(4)],
    )
    runner = BenchmarkRunner(
        runs=3,
        warmup_runs=1,
        workspace_root=tmp_path,
        output_dir=tmp_path / "out",
        capture_memory=False,
    )
    run = runner.run([scenario])

    assert len(run.samples) == 4
    assert run.summaries[0].successful_runs == 3
    assert run.summaries[0].failed_runs == 0
    assert run.summaries[0].mean_ms is not None


def test_cli_fake_voice_turn_smoke(tmp_path: Path, monkeypatch) -> None:
    audio = _fake_audio(tmp_path)
    out_dir = tmp_path / "results"
    rc = benchmark_main(
        [
            "--scenario",
            "voice_turn",
            "--adapter",
            "fake",
            "--runs",
            "1",
            "--warmup",
            "0",
            "--audio-path",
            str(audio),
            "--output-dir",
            str(out_dir),
        ]
    )
    assert rc == 0
    assert (out_dir / "samples.csv").exists()
    assert (out_dir / "samples.jsonl").exists()


def test_real_components_can_select_pytorch_cuda_tts(tmp_path: Path) -> None:
    config = VoiceConfig(
        whisper_bin="whisper-cli",
        whisper_model="tiny.bin",
        kokoro_model="kokoro.onnx",
        kokoro_voices="voices.bin",
    )

    components = build_real_components(
        config,
        tmp_path,
        tts_backend="pytorch",
        tts_device="cuda",
    )

    assert isinstance(components["tts"], BenchmarkPyTorchTTS)
    assert components["tts"].device == "cuda"


def test_parse_lfm_variant_supports_think_setting() -> None:
    assert parse_lfm_variant("current=lfm2.5-thinking:1.2b-q8_0") == LFMVariant(
        name="current",
        model="lfm2.5-thinking:1.2b-q8_0",
        think=None,
    )
    assert parse_lfm_variant("minimal=lfm2.5-thinking:1.2b-q8_0@false").think is False
    assert parse_lfm_variant("low=lfm2.5-thinking:1.2b-q8_0@low").think == "low"


def test_lfm_compare_records_native_metrics_and_tool_correctness() -> None:
    class FakeLFM:
        model = "lfm"
        num_gpu = None

        def __init__(self) -> None:
            self.last_turn_diagnostics = ModelTurnDiagnostics(
                model_total_duration_ms=1200.0,
                prompt_eval_duration_ms=200.0,
                prompt_eval_count=10,
                eval_duration_ms=1000.0,
                eval_count=20,
                generation_tokens_per_sec=20.0,
                model_call_count=1,
                proposal_valid=True,
                response_chars=42,
            )

        def propose(self, user_text: str) -> ToolRequest:
            return ToolRequest(name="time.now", arguments={})

    scenario = LFMCompareBenchmark(
        model=FakeLFM(),
        variant=LFMVariant("current", "lfm"),
        prompt="What time is it?",
        expected_tool="time.now",
    )

    outcome = scenario.run_once(iteration=0, is_warmup=False)

    assert outcome.status == "ok"
    assert outcome.details["model_total_duration_ms"] == 1200.0
    assert outcome.details["eval_count"] == 20
    assert outcome.details["tool_selection_correct"] is True


def test_agent_result_propagates_native_lfm_metrics(tmp_path: Path) -> None:
    class DiagnosticModel:
        last_turn_diagnostics = ModelTurnDiagnostics(
            model_total_duration_ms=1200.0,
            prompt_eval_duration_ms=200.0,
            eval_duration_ms=1000.0,
            eval_count=20,
            generation_tokens_per_sec=20.0,
            model_call_count=1,
        )

        def propose(self, user_text: str) -> FinalResponse:
            return FinalResponse(text="done")

        def respond_to_observation(self, user_text: str, observation):
            return FinalResponse(text="done")

    agent = OdessyAgent(
        model=DiagnosticModel(),
        policy=OdessyPolicy(workspace_root=tmp_path),
        mcp_server=None,
    )

    result = asyncio.run(agent.run_turn("hello"))

    assert result.metrics.model_total_duration_ms == 1200.0
    assert result.metrics.eval_count == 20
    assert result.metrics.model_call_count == 1


def _fake_audio(tmp_path: Path) -> Path:
    path = tmp_path / "input.wav"
    path.write_bytes(b"FAKE")
    return path


class ScriptedScenario:
    def __init__(self, name: str, outcomes: list[BenchmarkOutcome]) -> None:
        self.name = name
        self._outcomes = outcomes
        self._index = 0

    def run_once(self, *, iteration: int, is_warmup: bool) -> BenchmarkOutcome:
        outcome = self._outcomes[self._index]
        self._index += 1
        return outcome


class GuardedAudioIO:
    def __init__(self) -> None:
        self.record_called = False

    def record(self, output_path: Path, seconds: int) -> None:
        self.record_called = True
        raise AssertionError("voice_turn should reuse the provided audio file instead of recording a copy")

    def play(self, audio_path: Path) -> None:
        pass
