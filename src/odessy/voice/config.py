from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from odessy.config_file import load_config_env


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
    audio_input_device: str | None = None
    audio_output_device: str | None = None
    voice_seconds: int = 5
    tmp_dir: Path = Path(tempfile.gettempdir()) / "odessy-voice"

    @classmethod
    def from_env(cls) -> "VoiceConfig":
        load_config_env()
        return cls(
            whisper_bin=os.environ.get("ODESSY_WHISPER_BIN", "whisper-cli"),
            whisper_model=os.environ.get("ODESSY_WHISPER_MODEL", "models/ggml-tiny.en.bin"),
            kokoro_model=os.environ.get("ODESSY_KOKORO_MODEL", "kokoro-v1.0.int8.onnx"),
            kokoro_voices=os.environ.get("ODESSY_KOKORO_VOICES", "voices-v1.0.bin"),
            ollama_model=os.environ.get("ODESSY_OLLAMA_MODEL", "gemma3:1b"),
            onnx_provider=os.environ.get("ODESSY_ONNX_PROVIDER", "auto"),
            onnx_cache_dir=_env_path("ODESSY_ONNX_CACHE_DIR"),
            audio_recorder=os.environ.get("ODESSY_AUDIO_RECORDER", "arecord"),
            audio_player=os.environ.get("ODESSY_AUDIO_PLAYER", "aplay"),
            audio_input_device=os.environ.get("ODESSY_AUDIO_INPUT_DEVICE") or None,
            audio_output_device=os.environ.get("ODESSY_AUDIO_OUTPUT_DEVICE") or None,
            voice_seconds=_env_int("ODESSY_VOICE_SECONDS", 5),
            tmp_dir=Path(os.environ.get("ODESSY_VOICE_TMPDIR", str(Path(tempfile.gettempdir()) / "odessy-voice"))),
        )


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return max(1, int(value))
    except ValueError:
        return default


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None
