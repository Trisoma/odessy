from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from odessy.models.fake_model import FakeModel
from odessy.runtime.types import FinalResponse, ToolObservation, ToolRequest
from odessy.voice.stt import STTResult
from odessy.voice.tts import TTSResult


@dataclass(frozen=True)
class NoOpAudioIO:
    def record(self, output_path: Path, seconds: int) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"FAKE-WAV")

    def play(self, audio_path: Path) -> None:
        return


@dataclass(frozen=True)
class FakeSTT:
    transcript: str = "Who are you? Briefly introduce yourself."
    status: str = "ok"
    error: str | None = None

    def transcribe(self, audio_path: Path) -> STTResult:
        return STTResult(text=self.transcript, status=self.status, error=self.error)


@dataclass(frozen=True)
class FakeTTS:
    status: str = "ok"
    error: str | None = None

    def synthesize(self, text: str, output_path: Path) -> TTSResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(text.encode("utf-8"))
        return TTSResult(output_path=output_path, status=self.status, error=self.error)


def fake_model() -> FakeModel:
    return FakeModel()
