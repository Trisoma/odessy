"""Voice interface components for Odessy v0.1.0."""

from odessy.voice.config import VoiceConfig
from odessy.voice.stt import STTResult, WhisperCppSTT
from odessy.voice.tts import KokoroTTS, TTSResult

__all__ = [
    "KokoroTTS",
    "STTResult",
    "TTSResult",
    "VoiceConfig",
    "VoiceLoop",
    "VoiceLoopResult",
    "WhisperCppSTT",
]


def __getattr__(name: str):
    if name in {"VoiceLoop", "VoiceLoopResult"}:
        from odessy.voice.loop import VoiceLoop, VoiceLoopResult

        return {"VoiceLoop": VoiceLoop, "VoiceLoopResult": VoiceLoopResult}[name]
    raise AttributeError(name)
