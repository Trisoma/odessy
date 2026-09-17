"""
This is the adapter for TTS module benchmarking

The current audit w/o ONNX Runtime with TensorRT Execution Provider
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from odessy.voice.config import VoiceConfig
from odessy.voice.onnx_runtime import build_execution_providers

from .profiles import BenchmarkProfile

@dataclass(frozen=True)
class RuntimeInfo:
    requested_provider_mode: str
    available_execution_providers: list[str] | None
    requested_execution_providers: list[Any] | None
    session_execution_providers: list[str] | None
    runtime_version: str | None
    provider_options: dict[str, Any]
    provider_selection_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass(frozen=True)
class TTSMeasurement:
    inference_ms: float
    audio_duration_ms: float | None

class TTSBenchmarkEngine(Protocol):
    def initialize(self) -> RuntimeInfo:
        ...

    def synthesize(self, text: str, output_path: Path) -> TTSMeasurement:
        ...

    def close(self) -> None:
        ...

class RuntimeConfigurationError(RuntimeError):
    def __init__(self, message: str, *, runtime_info: RuntimeInfo) -> None:
        super().__init__(message)
        self.runtime_info = runtime_info

class LegacyKokoroEngine:
    def __init__(
        self,
        model_path: str,
        voices_path: str,
        *,
        voice: str = "af_sarah",
        speed: float = 1.0,
        lang: str = "en-us",
    ) -> None:
        self.model_path = model_path
        self.voices_path = voices_path
        self.voice = voice
        self.speed = speed
        self.lang = lang
        self._kokoro: Any | None = None

    def initialize(self) -> RuntimeInfo:
        try:
            from kokoro_onnx import Kokoro
        except ImportError as exc:
            raise RuntimeConfigurationError(
                f"Kokoro runtime unavailable: {exc}",
                runtime_info=_legacy_runtime_info(),
            ) from exc
        self._kokoro = Kokoro(self.model_path, self.voices_path)
        return _legacy_runtime_info()

    def synthesize(self, text: str, output_path: Path) -> TTSMeasurement:
        import soundfile as sf

        if self._kokoro is None:
            raise RuntimeError("Legacy Kokoro engine is not initialized")
        started = time.perf_counter()
        samples, sample_rate = self._kokoro.create(
            text,
            voice=self.voice,
            speed=self.speed,
            lang=self.lang,
        )
        sf.write(output_path, samples, sample_rate)
        return TTSMeasurement(
            inference_ms=(time.perf_counter() - started) * 1000.0,
            audio_duration_ms=_audio_duration_ms(samples, sample_rate),
        )

    def close(self) -> None:
        self._kokoro = None

class OnnxKokoroEngine:
    def __init__(
        self,
        model_path: str,
        voices_path: str,
        *,
        provider_mode: str,
        cache_dir: Path | None = None,
        voice: str = "af_sarah",
        speed: float = 1.0,
        lang: str = "en-us",
    ) -> None:
        self.model_path = model_path
        self.voices_path = voices_path
        self.provider_mode = provider_mode
        self.cache_dir = cache_dir
        self.voice = voice
        self.speed = speed
        self.lang = lang
        self._kokoro: Any | None = None
        self._session: Any | None = None
        self._runtime_info: RuntimeInfo | None = None

    def initialize(self) -> RuntimeInfo:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            info = RuntimeInfo(
                requested_provider_mode=self.provider_mode,
                available_execution_providers=None,
                requested_execution_providers=None,
                session_execution_providers=None,
                runtime_version=None,
                provider_options={},
                provider_selection_status="runtime_unavailable",
            )
            raise RuntimeConfigurationError(
                f"ONNX Runtime unavailable: {exc}",
                runtime_info=info,
            ) from exc

        available = list(ort.get_available_providers())
        base_info = RuntimeInfo(
            requested_provider_mode=self.provider_mode,
            available_execution_providers=available,
            requested_execution_providers=None,
            session_execution_providers=None,
            runtime_version=getattr(ort, "__version__", None),
            provider_options={},
            provider_selection_status="unavailable",
        )
        try:
            providers = build_execution_providers(
                self.provider_mode,
                cache_dir=self.cache_dir,
                available=available,
            )
        except (RuntimeError, ValueError) as exc:
            raise RuntimeConfigurationError(str(exc), runtime_info=base_info) from exc

        requested = _json_safe(providers)
        provider_options = _provider_options(providers)
        try:
            session = ort.InferenceSession(self.model_path, providers=providers)
            session_providers = _session_providers(session)
            status = _provider_selection_status(
                self.provider_mode,
                session_providers,
            )
            info = RuntimeInfo(
                requested_provider_mode=self.provider_mode,
                available_execution_providers=available,
                requested_execution_providers=requested,
                session_execution_providers=session_providers,
                runtime_version=getattr(ort, "__version__", None),
                provider_options=provider_options,
                provider_selection_status=status,
            )
            expected = _expected_provider(self.provider_mode, providers)
            if expected not in (session_providers or []):
                raise RuntimeConfigurationError(
                    f"Requested provider {expected} was not selected; "
                    f"session providers: {session_providers}",
                    runtime_info=info,
                )
        except RuntimeConfigurationError:
            raise
        except Exception as exc:
            info = RuntimeInfo(
                requested_provider_mode=self.provider_mode,
                available_execution_providers=available,
                requested_execution_providers=requested,
                session_execution_providers=None,
                runtime_version=getattr(ort, "__version__", None),
                provider_options=provider_options,
                provider_selection_status="initialization_failed",
            )
            raise RuntimeConfigurationError(str(exc), runtime_info=info) from exc

        from kokoro_onnx import Kokoro

        self._session = session
        self._kokoro = Kokoro.from_session(session, self.voices_path)
        self._runtime_info = info
        return info

    def synthesize(self, text: str, output_path: Path) -> TTSMeasurement:
        import soundfile as sf

        if self._kokoro is None:
            raise RuntimeError("ONNX Kokoro engine is not initialized")
        started = time.perf_counter()
        samples, sample_rate = self._kokoro.create(
            text,
            voice=self.voice,
            speed=self.speed,
            lang=self.lang,
        )
        sf.write(output_path, samples, sample_rate)
        return TTSMeasurement(
            inference_ms=(time.perf_counter() - started) * 1000.0,
            audio_duration_ms=_audio_duration_ms(samples, sample_rate),
        )

    def close(self) -> None:
        self._kokoro = None
        self._session = None
        self._runtime_info = None

def build_tts_engine(profile: BenchmarkProfile, config: VoiceConfig) -> TTSBenchmarkEngine:
    if profile.execution_path == "kokoro_pytorch":
        from .tts.pytorch_kokoro import PyTorchKokoroEngine

        device = None if profile.pytorch_device in {None, "auto"} else profile.pytorch_device
        return PyTorchKokoroEngine(device=device)

    common = {
        "model_path": config.kokoro_model,
        "voices_path": config.kokoro_voices,
    }
    if profile.execution_path == "legacy_constructor":
        return LegacyKokoroEngine(**common)
    if profile.execution_path in {"onnx_cpu", "onnx_cuda", "onnx_tensorrt"}:
        provider_mode = profile.onnx_provider or {
            "onnx_cpu": "cpu",
            "onnx_cuda": "cuda",
            "onnx_tensorrt": "tensorrt",
        }[profile.execution_path]
        return OnnxKokoroEngine(
            **common,
            provider_mode=provider_mode,
            cache_dir=config.onnx_cache_dir,
        )
    raise ValueError(f"Unsupported TTS execution path: {profile.execution_path}")

def _legacy_runtime_info() -> RuntimeInfo:
    try:
        import onnxruntime as ort
    except ImportError:
        return RuntimeInfo(
            requested_provider_mode="legacy",
            available_execution_providers=None,
            requested_execution_providers=None,
            session_execution_providers=None,
            runtime_version=None,
            provider_options={},
            provider_selection_status="not_observable",
        )
    return RuntimeInfo(
        requested_provider_mode="legacy",
        available_execution_providers=list(ort.get_available_providers()),
        requested_execution_providers=None,
        session_execution_providers=None,
        runtime_version=getattr(ort, "__version__", None),
        provider_options={},
        provider_selection_status="not_observable",
    )

def _session_providers(session: Any) -> list[str] | None:
    getter = getattr(session, "get_providers", None)
    if not callable(getter):
        return None
    return list(getter())

def _expected_provider(provider_mode: str, providers: list[Any]) -> str:
    if provider_mode == "auto":
        return providers[0][0] if isinstance(providers[0], tuple) else providers[0]
    return {
        "tensorrt": "TensorrtExecutionProvider",
        "cuda": "CUDAExecutionProvider",
        "cpu": "CPUExecutionProvider",
    }.get(provider_mode, providers[0][0] if isinstance(providers[0], tuple) else providers[0])

def _provider_selection_status(
    provider_mode: str,
    session_providers: list[str] | None,
) -> str:
    if session_providers is None:
        return "not_observable"
    expected = {
        "tensorrt": "TensorrtExecutionProvider",
        "cuda": "CUDAExecutionProvider",
        "cpu": "CPUExecutionProvider",
    }.get(provider_mode)
    if expected is None:
        return "selected" if session_providers else "not_observable"
    return "selected" if expected in session_providers else "requested_not_selected"

def _provider_options(providers: list[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for provider in providers:
        if isinstance(provider, tuple) and len(provider) == 2:
            result[provider[0]] = provider[1]
        elif isinstance(provider, str):
            result.setdefault(provider, {})
    return result

def _json_safe(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value

def _audio_duration_ms(samples: Any, sample_rate: int) -> float | None:
    try:
        sample_count = len(samples)
        return sample_count / float(sample_rate) * 1000.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None
