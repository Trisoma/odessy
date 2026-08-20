from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from odessy.agent_loop import OdessyAgent


class AudioIO(Protocol):
    def record(self, output_path: Path, seconds: int) -> None:
        ...

    def play(self, audio_path: Path) -> None:
        ...


class STT(Protocol):
    def transcribe(self, audio_path: Path):
        ...


class TTS(Protocol):
    def synthesize(self, text: str, output_path: Path):
        ...


@dataclass(frozen=True)
class VoiceLoopResult:
    status: str
    transcript: str
    final_text: str
    input_audio_path: Path
    output_audio_path: Path
    error: str | None = None


@dataclass(frozen=True)
class VoiceLoop:
    agent: OdessyAgent
    audio_io: AudioIO
    stt: STT
    tts: TTS
    tmp_dir: Path
    seconds: int = 5

    async def run_once(self) -> VoiceLoopResult:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        turn_id = uuid.uuid4().hex
        input_audio = self.tmp_dir / f"odessy-input-{turn_id}.wav"
        output_audio = self.tmp_dir / f"odessy-output-{turn_id}.wav"
        transcript = ""
        final_text = ""
        status = "ok"
        error: str | None = None

        try:
            self.audio_io.record(input_audio, self.seconds)
            stt_result = self.stt.transcribe(input_audio)
            transcript = stt_result.text
            if stt_result.status != "ok":
                return VoiceLoopResult(
                    status=stt_result.status,
                    transcript=transcript,
                    final_text="",
                    input_audio_path=input_audio,
                    output_audio_path=output_audio,
                    error=stt_result.error,
                )
            turn = await self.agent.run_turn(transcript)
            final_text = turn.final_text
            tts_result = self.tts.synthesize(final_text, output_audio)
            if tts_result.status != "ok":
                return VoiceLoopResult(
                    status="tts_failed",
                    transcript=transcript,
                    final_text=final_text,
                    input_audio_path=input_audio,
                    output_audio_path=output_audio,
                    error=tts_result.error,
                )
            self.audio_io.play(output_audio)
        except Exception as exc:
            status = "failed"
            error = str(exc)
        finally:
            _unlink_if_exists(input_audio)
            _unlink_if_exists(output_audio)

        return VoiceLoopResult(
            status=status,
            transcript=transcript,
            final_text=final_text,
            input_audio_path=input_audio,
            output_audio_path=output_audio,
            error=error,
        )


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> None:
    from odessy.cli import main as odessy_main

    odessy_main(["voice", *(list(sys.argv[1:] if argv is None else argv))])


if __name__ == "__main__":
    main()
