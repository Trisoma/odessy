from __future__ import annotations

import os
import tempfile
import sys
from dataclasses import dataclass
from pathlib import Path

from odessy.config.config_file import load_config_env
from odessy.voice.assets import DEFAULT_WAKE_MODEL, asset_dir

#: Seconds a follow-up window stays open in conversation mode.
DEFAULT_FOLLOWUP_SECONDS = 10
#: Hard ceiling on one utterance, silence included.
DEFAULT_MAX_UTTERANCE_SECONDS = 20

INTERACTIONS = ("wake", "conversation")


@dataclass(frozen=True)
class VoiceConfig:
    whisper_bin: str
    whisper_model: str
    kokoro_model: str
    kokoro_voices: str
    ollama_model: str = "gemma3:1b"
    onnx_provider: str = "auto"
    onnx_cache_dir: Path | None = None
    audio_recorder: str = "arecord"
    audio_player: str = "aplay"
    audio_backend: str = "command"
    audio_input_device: str | None = None
    audio_output_device: str | None = None
    voice_seconds: int = 5
    #: Extra recordings attempted when a turn hears no usable speech. Total
    #: recordings per turn is this plus one.
    blank_retries: int = 2
    settling_ms: int = 300
    tmp_dir: Path = Path(tempfile.gettempdir()) / "odessy-voice"
    #: ``wake`` requires the phrase before every request; ``conversation`` keeps a
    #: short follow-up window open after each reply.
    interaction: str = "wake"
    #: Path to an openWakeWord-compatible model. Never inside the repository.
    wake_model: Path = Path(DEFAULT_WAKE_MODEL)
    wake_threshold: float = 0.5
    followup_seconds: int = DEFAULT_FOLLOWUP_SECONDS
    max_utterance_seconds: int = DEFAULT_MAX_UTTERANCE_SECONDS
    #: Speak a streaming (cloud) model's answer sentence by sentence as it arrives.
    stream_speech: bool = True
    stt_provider: str = "whisper.cpp"
    tts_provider: str = "kokoro"
    stt_executable: str = ""
    stt_model: str = ""
    tts_executable: str = ""
    tts_model: str = ""

    def __post_init__(self) -> None:
        if self.audio_backend not in ('auto','command','native'):raise ValueError('Unknown audio backend')
        from .providers import validate_selector
        validate_selector(self.stt_provider)
        validate_selector(self.tts_provider)
        if not 0 <= self.settling_ms <= 10000:
            raise ValueError("ODESSY_VOICE_SETTLING_MS must be from 0 to 10000")
        if self.interaction not in INTERACTIONS:
            raise ValueError(
                f"ODESSY_VOICE_INTERACTION must be one of: {', '.join(INTERACTIONS)} "
                f"(got {self.interaction!r})"
            )
        if not 0.0 < self.wake_threshold <= 1.0:
            raise ValueError(
                f"ODESSY_WAKE_THRESHOLD must be greater than 0 and at most 1 (got {self.wake_threshold})"
            )

    @classmethod
    def from_env(cls) -> "VoiceConfig":
        load_config_env()
        wake_model = os.environ.get("ODESSY_WAKE_MODEL", "").strip()
        return cls(
            stt_provider=os.environ.get("ODESSY_STT_PROVIDER", "whisper.cpp"),
            tts_provider=os.environ.get("ODESSY_TTS_PROVIDER", "kokoro"),
            stt_executable=os.environ.get("ODESSY_STT_EXECUTABLE", ""),
            stt_model=os.environ.get("ODESSY_STT_MODEL", ""),
            tts_executable=os.environ.get("ODESSY_TTS_EXECUTABLE", ""),
            tts_model=os.environ.get("ODESSY_TTS_MODEL", ""),
            whisper_bin=os.environ.get("ODESSY_WHISPER_BIN", "whisper-cli"),
            whisper_model=os.environ.get("ODESSY_WHISPER_MODEL", "models/ggml-tiny.en.bin"),
            kokoro_model=os.environ.get("ODESSY_KOKORO_MODEL", "kokoro-v1.0.int8.onnx"),
            kokoro_voices=os.environ.get("ODESSY_KOKORO_VOICES", "voices-v1.0.bin"),
            ollama_model=os.environ.get("ODESSY_OLLAMA_MODEL", "gemma3:1b"),
            onnx_provider=os.environ.get("ODESSY_ONNX_PROVIDER", "auto"),
            onnx_cache_dir=_env_path("ODESSY_ONNX_CACHE_DIR"),
            audio_recorder=os.environ.get("ODESSY_AUDIO_RECORDER", "arecord"),
            audio_player=os.environ.get("ODESSY_AUDIO_PLAYER", "windows-native" if sys.platform == 'win32' else "aplay"),
            audio_backend=os.environ.get("ODESSY_AUDIO_BACKEND", "auto"),
            audio_input_device=os.environ.get("ODESSY_AUDIO_INPUT_DEVICE") or None,
            audio_output_device=os.environ.get("ODESSY_AUDIO_OUTPUT_DEVICE") or None,
            voice_seconds=_env_int("ODESSY_VOICE_SECONDS", 5),
            settling_ms=_env_retries("ODESSY_VOICE_SETTLING_MS", 300),
            blank_retries=_env_retries("ODESSY_VOICE_BLANK_RETRIES", 2),
            tmp_dir=Path(os.environ.get("ODESSY_VOICE_TMPDIR", str(Path(tempfile.gettempdir()) / "odessy-voice"))),
            interaction=os.environ.get("ODESSY_VOICE_INTERACTION", "wake").strip().lower() or "wake",
            wake_model=(
                Path(wake_model).expanduser()
                if wake_model
                else asset_dir() / DEFAULT_WAKE_MODEL
            ),
            wake_threshold=_env_float("ODESSY_WAKE_THRESHOLD", 0.5),
            followup_seconds=_env_int("ODESSY_VOICE_FOLLOWUP_SECONDS", DEFAULT_FOLLOWUP_SECONDS),
            max_utterance_seconds=_env_int(
                "ODESSY_VOICE_MAX_UTTERANCE_SECONDS", DEFAULT_MAX_UTTERANCE_SECONDS
            ),
            stream_speech=(os.environ.get("ODESSY_VOICE_STREAM_SPEECH") or "on").strip().lower()
            not in {"0", "off", "false", "no"},
        )


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return max(1, int(value))
    except ValueError:
        return default


def _env_retries(name: str, default: int) -> int:
    """A retry count, where 0 is a meaningful value (try once, never retry)."""

    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return max(0, int(value))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number (got {value!r})") from error


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None
