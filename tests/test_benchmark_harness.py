from __future__ import annotations

import csv
import json
from pathlib import Path

from benchmark.cli import main as benchmark_main
from benchmark.core import BenchmarkOutcome, BenchmarkRunner
from benchmark.export import write_csv, write_jsonl, write_metadata
from benchmark.fakes import FakeSTT, FakeTTS, NoOpAudioIO, fake_model
from benchmark.scenarios import GemmaBenchmark, STTBenchmark, TTSBenchmark, VoiceTurnBenchmark
from odessy.agent_loop import OdessyAgent
from odessy.policy import OdessyPolicy
from odessy.types import ToolObservation, ToolRequest


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

    with (tmp_path / "out" / "samples.jsonl").open("r", encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    assert len(lines) == len(run.samples)


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
