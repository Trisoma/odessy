from __future__ import annotations

import sys
import types
import csv
from pathlib import Path

import pytest

from benchmark.adapters import (
    LegacyKokoroEngine,
    OnnxKokoroEngine,
    RuntimeConfigurationError,
    RuntimeInfo,
    TTSMeasurement,
)
from benchmark.cli import main as benchmark_main
from benchmark.core import BenchmarkRunner
from benchmark.profiles import BenchmarkProfile, load_profiles
from benchmark.scenarios import KokoroMatrixBenchmark


def test_load_profiles_reads_the_six_kokoro_execution_paths(tmp_path: Path) -> None:
    manifest = tmp_path / "profiles.toml"
    manifest.write_text(
        """
[profiles.tts_legacy_cold]
component = "tts"
execution_path = "legacy_constructor"
lifecycle = "cold"

[profiles.tts_onnx_cpu_warm]
component = "tts"
execution_path = "onnx_cpu"
lifecycle = "warm"
onnx_provider = "cpu"
""",
        encoding="utf-8",
    )

    profiles = load_profiles(manifest)

    assert profiles["tts_legacy_cold"].execution_path == "legacy_constructor"
    assert profiles["tts_onnx_cpu_warm"].lifecycle == "warm"
    assert profiles["tts_onnx_cpu_warm"].onnx_provider == "cpu"


def test_onnx_tensorrt_records_exact_provider_order_and_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state: dict[str, object] = {}

    class FakeSession:
        def __init__(self, model_path: str, providers: list[object]) -> None:
            state["model_path"] = model_path
            state["requested_providers"] = providers

        def get_providers(self) -> list[str]:
            return [
                "TensorrtExecutionProvider",
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ]

    class FakeKokoro:
        @classmethod
        def from_session(cls, session: object, voices_path: str) -> object:
            state["session"] = session
            state["voices_path"] = voices_path
            return cls()

        def create(self, text: str, *, voice: str, speed: float, lang: str):
            return [0.0] * 100, 100

    ort = types.ModuleType("onnxruntime")
    ort.__version__ = "test-ort"
    ort.get_available_providers = lambda: [
        "TensorrtExecutionProvider",
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    ]
    ort.InferenceSession = FakeSession
    kokoro_onnx = types.ModuleType("kokoro_onnx")
    kokoro_onnx.Kokoro = FakeKokoro
    soundfile = types.ModuleType("soundfile")
    soundfile.write = lambda *args: None
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(sys.modules, "kokoro_onnx", kokoro_onnx)
    monkeypatch.setitem(sys.modules, "soundfile", soundfile)

    engine = OnnxKokoroEngine(
        model_path="model.onnx",
        voices_path="voices.bin",
        provider_mode="tensorrt",
        cache_dir=tmp_path / "cache",
    )
    info = engine.initialize()

    requested = state["requested_providers"]
    assert isinstance(requested, list)
    assert requested[0][0] == "TensorrtExecutionProvider"
    assert requested[1] == ("CUDAExecutionProvider", {"device_id": 0})
    assert requested[2] == "CPUExecutionProvider"
    assert info.available_execution_providers == [
        "TensorrtExecutionProvider",
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    ]
    assert info.session_execution_providers == [
        "TensorrtExecutionProvider",
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    ]
    assert info.provider_selection_status == "selected"
    assert info.runtime_version == "test-ort"


def test_onnx_tensorrt_fails_without_silent_cuda_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ort = types.ModuleType("onnxruntime")
    ort.__version__ = "test-ort"
    ort.get_available_providers = lambda: [
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    ]
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)

    engine = OnnxKokoroEngine(
        model_path="model.onnx",
        voices_path="voices.bin",
        provider_mode="tensorrt",
        cache_dir=tmp_path / "cache",
    )

    with pytest.raises(RuntimeConfigurationError) as raised:
        engine.initialize()

    assert "TensorRT provider unavailable" in str(raised.value)
    assert raised.value.runtime_info.available_execution_providers == [
        "CUDAExecutionProvider",
        "CPUExecutionProvider",
    ]


def test_legacy_engine_marks_hidden_session_providers_unobservable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeKokoro:
        def __init__(self, model_path: str, voices_path: str) -> None:
            pass

    ort = types.ModuleType("onnxruntime")
    ort.__version__ = "legacy-ort"
    ort.get_available_providers = lambda: ["CPUExecutionProvider"]
    kokoro_onnx = types.ModuleType("kokoro_onnx")
    kokoro_onnx.Kokoro = FakeKokoro
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(sys.modules, "kokoro_onnx", kokoro_onnx)

    info = LegacyKokoroEngine("model.onnx", "voices.bin").initialize()

    assert info.available_execution_providers == ["CPUExecutionProvider"]
    assert info.session_execution_providers is None
    assert info.provider_selection_status == "not_observable"


def test_cold_matrix_samples_separate_initialization_and_inference(tmp_path: Path) -> None:
    engine = RecordingEngine()
    profile = BenchmarkProfile(
        name="tts_onnx_cpu_cold",
        component="tts",
        execution_path="onnx_cpu",
        lifecycle="cold",
        onnx_provider="cpu",
    )
    scenario = KokoroMatrixBenchmark(
        profile=profile,
        text="hello",
        output_dir=tmp_path,
        engine_factory=lambda: engine,
    )

    run = BenchmarkRunner(
        runs=2,
        warmup_runs=1,
        workspace_root=tmp_path,
        output_dir=tmp_path / "out",
        capture_memory=False,
    ).run([scenario])

    measured = [sample for sample in run.samples if not sample.is_warmup]
    assert engine.initialize_count == 3
    assert all(sample.initialization_ms is not None for sample in measured)
    assert all(sample.inference_ms == 7.0 for sample in measured)
    assert all(
        sample.total_ms == pytest.approx(sample.initialization_ms + 7.0)
        for sample in measured
    )
    assert run.summaries[0].inference_p50_ms == 7.0
    assert run.summaries[0].total_p99_ms is not None


def test_warm_matrix_initializes_once_and_excludes_init_from_samples(tmp_path: Path) -> None:
    engine = RecordingEngine()
    profile = BenchmarkProfile(
        name="tts_onnx_cpu_warm",
        component="tts",
        execution_path="onnx_cpu",
        lifecycle="warm",
        onnx_provider="cpu",
    )
    scenario = KokoroMatrixBenchmark(
        profile=profile,
        text="hello",
        output_dir=tmp_path,
        engine_factory=lambda: engine,
    )

    run = BenchmarkRunner(
        runs=2,
        warmup_runs=1,
        workspace_root=tmp_path,
        output_dir=tmp_path / "out",
        capture_memory=False,
    ).run([scenario])

    measured = [sample for sample in run.samples if not sample.is_warmup]
    assert engine.initialize_count == 1
    assert scenario.initialization_ms is not None
    assert all(sample.initialization_ms is None for sample in measured)
    assert all(sample.inference_ms == 7.0 for sample in measured)
    assert all(sample.total_ms == 7.0 for sample in measured)


def test_failed_matrix_profile_is_recorded_as_a_result(tmp_path: Path) -> None:
    info = RuntimeInfo(
        requested_provider_mode="tensorrt",
        available_execution_providers=["CPUExecutionProvider"],
        requested_execution_providers=None,
        session_execution_providers=None,
        runtime_version="test-ort",
        provider_options={},
        provider_selection_status="unavailable",
    )
    profile = BenchmarkProfile(
        name="tts_onnx_tensorrt_cold",
        component="tts",
        execution_path="onnx_tensorrt",
        lifecycle="cold",
        onnx_provider="tensorrt",
    )

    def failing_factory() -> RecordingEngine:
        raise RuntimeConfigurationError("TensorRT provider unavailable", runtime_info=info)

    scenario = KokoroMatrixBenchmark(
        profile=profile,
        text="hello",
        output_dir=tmp_path,
        engine_factory=failing_factory,
    )
    run = BenchmarkRunner(
        runs=1,
        warmup_runs=0,
        workspace_root=tmp_path,
        output_dir=tmp_path / "out",
        capture_memory=False,
    ).run([scenario])

    sample = run.samples[0]
    assert sample.success is False
    assert sample.error == "TensorRT provider unavailable"
    assert sample.available_execution_providers == ["CPUExecutionProvider"]


def test_failed_warm_profile_excludes_one_time_initialization_from_samples(
    tmp_path: Path,
) -> None:
    info = RuntimeInfo(
        requested_provider_mode="tensorrt",
        available_execution_providers=["CPUExecutionProvider"],
        requested_execution_providers=None,
        session_execution_providers=None,
        runtime_version="test-ort",
        provider_options={},
        provider_selection_status="unavailable",
    )

    def failing_factory() -> RecordingEngine:
        raise RuntimeConfigurationError("TensorRT provider unavailable", runtime_info=info)

    profile = BenchmarkProfile(
        name="tts_onnx_tensorrt_warm",
        component="tts",
        execution_path="onnx_tensorrt",
        lifecycle="warm",
        onnx_provider="tensorrt",
    )
    scenario = KokoroMatrixBenchmark(
        profile=profile,
        text="hello",
        output_dir=tmp_path,
        engine_factory=failing_factory,
    )

    run = BenchmarkRunner(
        runs=1,
        warmup_runs=0,
        workspace_root=tmp_path,
        output_dir=tmp_path / "out",
        capture_memory=False,
    ).run([scenario])

    sample = run.samples[0]
    assert scenario.initialization_ms is not None
    assert sample.initialization_ms is None
    assert sample.total_ms == 0.0


def test_matrix_cli_writes_profile_and_provider_columns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeSession:
        def __init__(self, model_path: str, providers: list[object]) -> None:
            self.providers = providers

        def get_providers(self) -> list[str]:
            return ["CPUExecutionProvider"]

    class FakeKokoro:
        def __init__(self, model_path: str, voices_path: str) -> None:
            pass

        @classmethod
        def from_session(cls, session: object, voices_path: str) -> "FakeKokoro":
            return cls("model.onnx", voices_path)

        def create(self, text: str, *, voice: str, speed: float, lang: str):
            return [0.0] * 100, 100

    ort = types.ModuleType("onnxruntime")
    ort.__version__ = "test-ort"
    ort.get_available_providers = lambda: ["CPUExecutionProvider"]
    ort.InferenceSession = FakeSession
    kokoro_onnx = types.ModuleType("kokoro_onnx")
    kokoro_onnx.Kokoro = FakeKokoro
    soundfile = types.ModuleType("soundfile")
    soundfile.write = lambda *args: None
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(sys.modules, "kokoro_onnx", kokoro_onnx)
    monkeypatch.setitem(sys.modules, "soundfile", soundfile)

    manifest = tmp_path / "profiles.toml"
    manifest.write_text(
        """
version = 1
component = "tts"

[profiles.onnx_cpu_cold]
execution_path = "onnx_cpu"
lifecycle = "cold"
requested_provider_mode = "cpu"
""",
        encoding="utf-8",
    )
    output_dir = tmp_path / "results"

    assert benchmark_main(
        [
            "--manifest",
            str(manifest),
            "--profile",
            "onnx_cpu_cold",
            "--adapter",
            "real",
            "--runs",
            "1",
            "--warmup",
            "0",
            "--output-dir",
            str(output_dir),
            "--text",
            "hello",
        ]
    ) == 0

    with (output_dir / "samples.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        row = next(csv.DictReader(handle))
    assert row["profile"] == "onnx_cpu_cold"
    assert row["execution_path"] == "onnx_cpu"
    assert row["session_execution_providers"] == '["CPUExecutionProvider"]'
    assert row["provider_selection_status"] == "selected"


class RecordingEngine:
    def __init__(self) -> None:
        self.initialize_count = 0

    def initialize(self) -> RuntimeInfo:
        self.initialize_count += 1
        return RuntimeInfo(
            requested_provider_mode="cpu",
            available_execution_providers=["CPUExecutionProvider"],
            requested_execution_providers=["CPUExecutionProvider"],
            session_execution_providers=["CPUExecutionProvider"],
            runtime_version="test-ort",
            provider_options={},
            provider_selection_status="selected",
        )

    def synthesize(self, text: str, output_path: Path) -> TTSMeasurement:
        output_path.write_bytes(b"test")
        return TTSMeasurement(inference_ms=7.0, audio_duration_ms=10.0)

    def close(self) -> None:
        pass
