from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from odessy.agent_loop import OdessyAgent
from odessy.ollama_model import OllamaModel
from odessy.policy import OdessyPolicy
from odessy.types import FinalResponse, ToolObservation, ToolRequest
from odessy.voice.audio_io import CommandAudioIO
from odessy.voice.config import VoiceConfig
from odessy.voice.stt import WhisperCppSTT
from odessy.voice.tts import KokoroTTS

from .adapters import (
    RuntimeConfigurationError,
    RuntimeInfo,
    TTSBenchmarkEngine,
    build_tts_engine,
)
from .core import BenchmarkOutcome
from .fakes import FakeSTT, FakeTTS, NoOpAudioIO, fake_model
from .profiles import BenchmarkProfile


class VoiceBenchmarkAudioIO(Protocol):
    def record(self, output_path: Path, seconds: int) -> None:
        ...

    def play(self, audio_path: Path) -> None:
        ...


@dataclass(frozen=True)
class GemmaBenchmark:
    model: Any
    prompt: str
    synthetic_observation: ToolObservation
    name: str = "gemma"

    def run_once(self, *, iteration: int, is_warmup: bool) -> BenchmarkOutcome:
        import time

        stages: dict[str, float] = {}
        started = time.perf_counter()
        proposal_begin = time.perf_counter()
        action = self.model.propose(self.prompt)
        stages["proposal_ms"] = (time.perf_counter() - proposal_begin) * 1000.0

        response_begin = time.perf_counter()
        if isinstance(action, FinalResponse):
            response_text = action.text
            response_mode = "final"
        else:
            response_text = self.model.respond_to_observation(self.prompt, self.synthetic_observation).text
            response_mode = "observation"
        stages["response_ms"] = (time.perf_counter() - response_begin) * 1000.0
        total_ms = (time.perf_counter() - started) * 1000.0

        return BenchmarkOutcome(
            status="ok",
            stage_ms=stages,
            details={
                "prompt": self.prompt,
                "action_type": type(action).__name__,
                "response_mode": response_mode,
                "response_text": response_text,
                "total_ms": total_ms,
                "components_invoked": {
                    "model": _component_name(self.model),
                    "ollama_model": getattr(self.model, "model", None),
                },
            },
        )


@dataclass(frozen=True)
class STTBenchmark:
    stt: Any
    audio_path: Path
    name: str = "stt"

    def run_once(self, *, iteration: int, is_warmup: bool) -> BenchmarkOutcome:
        import time

        started = time.perf_counter()
        stage_begin = time.perf_counter()
        result = self.stt.transcribe(self.audio_path)
        stage_ms = {"transcribe_ms": (time.perf_counter() - stage_begin) * 1000.0}
        status = result.status
        if status != "ok" and not result.error:
            status = status or "failed"
        return BenchmarkOutcome(
            status=status,
            stage_ms=stage_ms,
            details={
                "audio_path": str(self.audio_path),
                "transcript": result.text,
                "total_ms": (time.perf_counter() - started) * 1000.0,
                "components_invoked": {
                    "stt": _component_name(self.stt),
                    "whisper_bin": getattr(self.stt, "whisper_bin", None),
                    "whisper_model": getattr(self.stt, "model_path", None),
                },
            },
            error=result.error,
        )


@dataclass(frozen=True)
class TTSBenchmark:
    tts: Any
    text: str
    output_dir: Path
    name: str = "tts"

    def run_once(self, *, iteration: int, is_warmup: bool) -> BenchmarkOutcome:
        import time

        output_path = self.output_dir / f"tts-{iteration}-{uuid.uuid4().hex}.wav"
        started = time.perf_counter()
        stage_begin = time.perf_counter()
        result = self.tts.synthesize(self.text, output_path)
        stage_ms = {"synthesize_ms": (time.perf_counter() - stage_begin) * 1000.0}
        return BenchmarkOutcome(
            status=result.status,
            stage_ms=stage_ms,
            details={
                "text": self.text,
                "output_path": str(output_path),
                "total_ms": (time.perf_counter() - started) * 1000.0,
                "components_invoked": {
                    "tts": _component_name(self.tts),
                    "kokoro_model": getattr(self.tts, "model_path", None),
                    "kokoro_voices": getattr(self.tts, "voices_path", None),
                },
            },
            error=result.error,
        )


@dataclass
class KokoroMatrixBenchmark:
    profile: BenchmarkProfile
    text: str
    output_dir: Path
    config: VoiceConfig | None = None
    engine_factory: Any | None = None
    name: str = field(default="", init=False)
    warmup_iterations: int | None = None
    measurement_iterations: int | None = None
    initialization_ms: float | None = field(default=None, init=False)
    _warm_engine: TTSBenchmarkEngine | None = field(default=None, init=False, repr=False)
    _warm_runtime_info: RuntimeInfo | None = field(default=None, init=False, repr=False)
    _prepare_error: Exception | None = field(default=None, init=False, repr=False)
    _prepare_runtime_info: RuntimeInfo | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.name = self.profile.name

    def prepare(self) -> None:
        if self.profile.lifecycle != "warm" or self._warm_engine is not None:
            return
        started = time.perf_counter()
        try:
            engine = self._build_engine()
            self._warm_runtime_info = engine.initialize()
            self.initialization_ms = (time.perf_counter() - started) * 1000.0
            self._warm_engine = engine
        except Exception as exc:
            self.initialization_ms = (time.perf_counter() - started) * 1000.0
            self._prepare_error = exc
            self._prepare_runtime_info = _runtime_info_from_error(exc)
            if "engine" in locals():
                engine.close()

    def close(self) -> None:
        if self._warm_engine is not None:
            self._warm_engine.close()
            self._warm_engine = None

    def run_once(self, *, iteration: int, is_warmup: bool) -> BenchmarkOutcome:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"{self.profile.name}-{iteration}-{uuid.uuid4().hex}.wav"

        if self.profile.lifecycle == "warm":
            if self._warm_engine is None:
                self.prepare()
            if self._prepare_error is not None:
                outcome = self._failed_outcome(
                    self._prepare_error,
                    self._prepare_runtime_info,
                    # Warm samples exclude one-time initialization, including
                    # failures. The duration remains in run metadata.
                    initialization_ms=None,
                )
                _unlink_if_exists(output_path)
                return outcome
            engine = self._warm_engine
            assert engine is not None
            initialization_ms = None
            runtime_info = self._warm_runtime_info
        else:
            started = time.perf_counter()
            try:
                engine = self._build_engine()
                runtime_info = engine.initialize()
            except Exception as exc:
                initialization_ms = (time.perf_counter() - started) * 1000.0
                outcome = self._failed_outcome(
                    exc,
                    _runtime_info_from_error(exc),
                    initialization_ms=initialization_ms,
                )
                if "engine" in locals():
                    engine.close()
                _unlink_if_exists(output_path)
                return outcome
            initialization_ms = (time.perf_counter() - started) * 1000.0

        try:
            measurement = engine.synthesize(self.text, output_path)
            inference_ms = measurement.inference_ms
            total_ms = inference_ms + initialization_ms if initialization_ms is not None else inference_ms
            details = self._details(
                runtime_info,
                initialization_ms=initialization_ms,
                inference_ms=inference_ms,
                total_ms=total_ms,
                audio_duration_ms=measurement.audio_duration_ms,
            )
            return BenchmarkOutcome(
                status="ok",
                stage_ms={"synthesize_ms": inference_ms},
                details=details,
            )
        except Exception as exc:
            return self._failed_outcome(
                exc,
                runtime_info,
                initialization_ms=initialization_ms,
            )
        finally:
            if self.profile.lifecycle == "cold":
                engine.close()
            _unlink_if_exists(output_path)

    def _build_engine(self) -> TTSBenchmarkEngine:
        if self.engine_factory is not None:
            return self.engine_factory()
        if self.config is None:
            raise ValueError("Kokoro matrix benchmark requires VoiceConfig or engine_factory")
        return build_tts_engine(self.profile, self.config)

    def _details(
        self,
        runtime_info: RuntimeInfo | None,
        *,
        initialization_ms: float | None,
        inference_ms: float | None,
        total_ms: float | None,
        audio_duration_ms: float | None,
    ) -> dict[str, Any]:
        info = runtime_info or RuntimeInfo(
            requested_provider_mode="unknown",
            available_execution_providers=None,
            requested_execution_providers=None,
            session_execution_providers=None,
            runtime_version=None,
            provider_options={},
            provider_selection_status="not_observable",
        )
        rtf = None
        if inference_ms is not None and audio_duration_ms:
            rtf = inference_ms / audio_duration_ms
        return {
            **self.profile.to_dict(),
            "profile": self.profile.name,
            "hardware": platform_name(),
            "warmup_iterations": self.warmup_iterations,
            "measurement_iterations": self.measurement_iterations,
            "initialization_ms": initialization_ms,
            "inference_ms": inference_ms,
            "total_ms": total_ms,
            "audio_duration_ms": audio_duration_ms,
            "rtf": rtf,
            **info.to_dict(),
        }

    def _failed_outcome(
        self,
        error: Exception,
        runtime_info: RuntimeInfo | None,
        *,
        initialization_ms: float | None = None,
    ) -> BenchmarkOutcome:
        details = self._details(
            runtime_info,
            initialization_ms=initialization_ms,
            inference_ms=None,
            total_ms=initialization_ms if initialization_ms is not None else 0.0,
            audio_duration_ms=None,
        )
        return BenchmarkOutcome(
            status="failed",
            stage_ms={"synthesize_ms": initialization_ms}
            if initialization_ms is not None
            else {},
            details=details,
            error=str(error),
        )


@dataclass(frozen=True)
class VoiceTurnBenchmark:
    agent: OdessyAgent
    audio_io: VoiceBenchmarkAudioIO
    stt: Any
    tts: Any
    tmp_dir: Path
    input_audio_path: Path | None = None
    seconds: int = 5
    name: str = "voice_turn"
    preserve_artifacts: bool = False

    def run_once(self, *, iteration: int, is_warmup: bool) -> BenchmarkOutcome:
        import time

        turn_id = uuid.uuid4().hex
        input_audio = self.input_audio_path or self.tmp_dir / f"odessy-bench-input-{turn_id}.wav"
        output_audio = self.tmp_dir / f"odessy-bench-output-{turn_id}.wav"
        stages: dict[str, float] = {}
        started = time.perf_counter()
        transcript = ""
        final_text = ""
        error: str | None = None
        status = "ok"
        tool_request_name: str | None = None
        components_invoked = _voice_components(self.agent, self.audio_io, self.stt, self.tts)

        try:
            if self.input_audio_path is None:
                record_begin = time.perf_counter()
                self.audio_io.record(input_audio, self.seconds)
                stages["record_ms"] = (time.perf_counter() - record_begin) * 1000.0
            else:
                stages["record_ms"] = 0.0

            stt_begin = time.perf_counter()
            stt_result = self.stt.transcribe(input_audio)
            stages["transcribe_ms"] = (time.perf_counter() - stt_begin) * 1000.0
            transcript = stt_result.text
            if stt_result.status != "ok":
                status = stt_result.status
                error = stt_result.error
                return BenchmarkOutcome(
                    status=status,
                    stage_ms=dict(stages),
                    details={
                        "input_audio_path": str(input_audio),
                        "output_audio_path": str(output_audio),
                        "transcript": transcript,
                        "total_ms": (time.perf_counter() - started) * 1000.0,
                        "components_invoked": components_invoked,
                    },
                    error=error,
                )

            agent_begin = time.perf_counter()
            turn = asyncio.run(self.agent.run_turn(transcript))
            stages["agent_ms"] = (time.perf_counter() - agent_begin) * 1000.0
            final_text = turn.final_text
            if turn.tool_request is not None:
                tool_request_name = turn.tool_request.name

            tts_begin = time.perf_counter()
            tts_result = self.tts.synthesize(final_text, output_audio)
            stages["synthesize_ms"] = (time.perf_counter() - tts_begin) * 1000.0
            if tts_result.status != "ok":
                status = "tts_failed"
                error = tts_result.error
                return BenchmarkOutcome(
                    status=status,
                    stage_ms=dict(stages),
                    details={
                        "input_audio_path": str(input_audio),
                        "output_audio_path": str(output_audio),
                        "transcript": transcript,
                        "final_text": final_text,
                        "tool_request_name": tool_request_name,
                        "total_ms": (time.perf_counter() - started) * 1000.0,
                        "components_invoked": components_invoked,
                    },
                    error=error,
                )

            play_begin = time.perf_counter()
            self.audio_io.play(output_audio)
            stages["play_ms"] = (time.perf_counter() - play_begin) * 1000.0
        except Exception as exc:
            status = "failed"
            error = str(exc)
        finally:
            if not self.preserve_artifacts and self.input_audio_path is None:
                _unlink_if_exists(input_audio)
            if not self.preserve_artifacts:
                _unlink_if_exists(output_audio)

        return BenchmarkOutcome(
            status=status,
            stage_ms=dict(stages),
            details={
                "input_audio_path": str(input_audio),
                "output_audio_path": str(output_audio),
                "transcript": transcript,
                "final_text": final_text,
                "tool_request_name": tool_request_name,
                "total_ms": (time.perf_counter() - started) * 1000.0,
                "components_invoked": components_invoked,
            },
            error=error,
        )


def build_real_components(config: VoiceConfig, workspace_root: Path) -> dict[str, Any]:
    model = OllamaModel(model=config.ollama_model)
    stt = WhisperCppSTT(whisper_bin=config.whisper_bin, model_path=config.whisper_model)
    tts = KokoroTTS(
        model_path=config.kokoro_model,
        voices_path=config.kokoro_voices,
        provider=config.onnx_provider,
        cache_dir=config.onnx_cache_dir,
    )
    audio_io = CommandAudioIO(
        recorder=config.audio_recorder,
        player=config.audio_player,
        input_device=config.audio_input_device,
        output_device=config.audio_output_device,
    )
    policy = OdessyPolicy(workspace_root=workspace_root)
    agent = OdessyAgent(model=model, policy=policy, mcp_server=None)
    return {"model": model, "stt": stt, "tts": tts, "audio_io": audio_io, "agent": agent}


def build_fake_components() -> dict[str, Any]:
    model = fake_model()
    stt = FakeSTT()
    tts = FakeTTS()
    audio_io = NoOpAudioIO()
    policy = OdessyPolicy(workspace_root=Path.cwd())
    agent = OdessyAgent(model=model, policy=policy, mcp_server=None)
    return {"model": model, "stt": stt, "tts": tts, "audio_io": audio_io, "agent": agent}


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _component_name(component: Any) -> str:
    return type(component).__name__


def _runtime_info_from_error(error: Exception) -> RuntimeInfo | None:
    if isinstance(error, RuntimeConfigurationError):
        return error.runtime_info
    return None


def platform_name() -> str:
    import platform

    return platform.platform()


def _voice_components(
    agent: OdessyAgent,
    audio_io: VoiceBenchmarkAudioIO,
    stt: Any,
    tts: Any,
) -> dict[str, Any]:
    model = getattr(agent, "_model", None)
    return {
        "audio_io": _component_name(audio_io),
        "stt": _component_name(stt),
        "model": _component_name(model) if model is not None else None,
        "ollama_model": getattr(model, "model", None),
        "tts": _component_name(tts),
        "whisper_bin": getattr(stt, "whisper_bin", None),
        "whisper_model": getattr(stt, "model_path", None),
        "kokoro_model": getattr(tts, "model_path", None),
        "kokoro_voices": getattr(tts, "voices_path", None),
    }
