from __future__ import annotations

import sys
import asyncio
import contextlib
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from odessy.runtime.agent_loop import OdessyAgent
from odessy.voice.stt import blank_transcript_reason


# Compatibility re-exports for existing integrations.
from odessy.voice.providers import STT, TTS, cancel_provider

class AudioIO(Protocol):
    def record(self, output_path: Path, seconds: int) -> None:
        ...

    def play(self, audio_path: Path) -> None:
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
    status_callback: Callable[[str], None] | None = None
    interactions: object | None = None
    stop_turn: Callable[[], None] | None = None
    cue: Callable | None = None
    settling_ms: int = 300

    async def _blocking(self, function, *args):
        # A cancelled to_thread does not stop the device. Drain its owned call
        # before releasing transport ownership or deleting its temporary file.
        cancelled = threading.Event()
        owned_audio = getattr(function, '__self__', None) is self.audio_io
        runner = getattr(self.audio_io, 'run_cancellable', None) if owned_audio else None
        task = asyncio.create_task(asyncio.to_thread(runner, function, args, cancelled)
                                   if runner is not None else asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            cancel_provider(getattr(function, "__self__", None))
            if owned_audio:
                stop = getattr(self.audio_io, 'stop', None)
                if stop is not None:
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(stop)
            with contextlib.suppress(Exception):
                await task
            raise

    async def handle_interaction(self, entry: dict) -> None:
        from odessy.voice.interaction import interaction_prompt, resolve_spoken_answer, voice_allowed

        broker = self.interactions
        if broker is None:
            raise RuntimeError('VoiceLoop interaction broker is not configured')
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        prompt_audio = self.tmp_dir / f'odessy-prompt-{token}.wav'
        reply_audio = self.tmp_dir / f'odessy-answer-{token}.wav'
        try:
            for attempt in range(3):
                prompt = interaction_prompt(entry) if not attempt else 'I could not use that response. Please answer the pending prompt after the cue.'
                self._status(prompt)
                if entry['kind'] == 'approval':
                    self._status('Approval phrase: approve this action')
                result = await self._blocking(self.tts.synthesize, prompt, prompt_audio)
                if result.status != 'ok':
                    raise RuntimeError(result.error or 'Interaction speech output failed')
                await self._blocking(self.audio_io.play, prompt_audio)
                await asyncio.sleep(self.settling_ms / 1000)
                if self.cue is not None:
                    await self.cue()
                broker.mark_ready(entry['id'])
                if not voice_allowed(entry):
                    self._status('This action requires keyboard or authenticated UI confirmation.')
                    await asyncio.Future()
                if not broker.is_pending(entry['id']):
                    return
                await self._blocking(self.audio_io.record, reply_audio, self.seconds)
                result = await self._blocking(self.stt.transcribe, reply_audio)
                if result.status not in ('ok', 'empty'):
                    continue
                from odessy.interaction.phrases import is_cancel
                if is_cancel(result.text):
                    broker.cancel_pending('stopped by voice')
                    from odessy.voice.interaction import schedule_stop
                    schedule_stop(self.stop_turn)
                    return
                if resolve_spoken_answer(entry, broker, result.text):
                    return
            self._status('Pending: no usable voice response; use the terminal or interaction API.')
            await asyncio.Future()
        finally:
            _unlink_if_exists(prompt_audio)
            _unlink_if_exists(reply_audio)

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
            self._status("Recording started")
            await self._blocking(self.audio_io.record, input_audio, self.seconds)
            self._status("Recording stopped")
            self._status("Transcribing speech")
            stt_result = await self._blocking(self.stt.transcribe, input_audio)
            transcript = stt_result.text
            if stt_result.status != "ok" and not (
                stt_result.status == "empty" and not transcript.strip()
            ):
                return VoiceLoopResult(
                    status=stt_result.status,
                    transcript=transcript,
                    final_text="",
                    input_audio_path=input_audio,
                    output_audio_path=output_audio,
                    error=stt_result.error,
                )
            # Applied here rather than in the Whisper adapter so every STT
            # implementation is covered, including a future cloud one. No usable
            # speech means no request: the model is not called, so it cannot propose
            # a tool, and authorization never enters the picture because nothing was
            # proposed. Retrying is the caller's decision (`ODESSY_VOICE_BLANK_RETRIES`).
            blank = blank_transcript_reason(transcript)
            if blank is not None:
                self._status("No speech detected")
                return VoiceLoopResult(
                    status="blank",
                    transcript=transcript,
                    final_text="",
                    input_audio_path=input_audio,
                    output_audio_path=output_audio,
                    error=blank,
                )
            self._status("Model is thinking")
            turn = await self.agent.run_turn(transcript, status_callback=self._status)
            final_text = turn.final_text
            self._status("Synthesizing speech")
            tts_result = await self._blocking(self.tts.synthesize, final_text, output_audio)
            if tts_result.status != "ok":
                return VoiceLoopResult(
                    status="tts_failed",
                    transcript=transcript,
                    final_text=final_text,
                    input_audio_path=input_audio,
                    output_audio_path=output_audio,
                    error=tts_result.error,
                )
            self._status("Playing response")
            await self._blocking(self.audio_io.play, output_audio)
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

    def _status(self, message: str) -> None:
        if self.status_callback is not None:
            self.status_callback(message)


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
