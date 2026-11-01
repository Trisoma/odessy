"""The wake-word voice session.

This is the controller the plan describes: it owns one microphone stream, wakes on a
phrase, captures one utterance, and hands the transcript to the existing agent turn.
It never decides whether an action is allowed. Every proposed action still goes
through validation, policy, confirmation, and the execution-target gate, exactly as
typed input does — the session only decides *when the microphone is open* and *what
text to submit*.

The design rule that shapes everything here is: **one reader, one worker**.

* The reader owns the device. It never blocks on a model call, so the wake phrase is
  still heard while a turn is executing or a reply is playing.
* The worker runs one turn at a time. Two turns can never overlap, because a second
  model generation on the same instance while the first is still streaming is not a
  thing the adapters support.

Interruption is therefore cooperative: a wake event sets the turn's stop event and
kills playback, and the running turn notices at its next check. Anything the turn
already completed stays completed.

With ``stream_speech``, a model that streams its reply (the cloud adapters) is heard
sentence by sentence while the turn runs (``runtime/speech.py``). A sentence is spoken
only while its turn is current: the wake phrase drops the rest. When the turn's answer
is the text already spoken it is not said again; any other answer (a stop, a runtime
message, a different final reply) is spoken as before.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import wave
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Literal

from odessy.runtime.speech import SpeechStream, same_text, speech_scope
from odessy.runtime.types import ConversationContext
from odessy.voice.frames import (
    WAKE_FRAME_BYTES,
    WAKE_FRAME_MS,
    FrameBuffer,
    FrameQueue,
    AudioFrame,
)
from odessy.voice.memory import ConversationMemory
from odessy.voice.segmentation import SegmenterConfig, UtteranceSegmenter
from odessy.voice.vad import SpeechDetector
from odessy.voice.wake import WakeDetector, WakeGate

SessionState = Literal[
    "LISTENING",
    "READY",
    "RECORDING",
    "TRANSCRIBING",
    "THINKING",
    "SPEAKING",
    "FOLLOWUP",
    "STOPPING",
    "ERROR",
    "INTERACTION_PROMPT",
]

Interaction = Literal["wake", "conversation"]

#: Exact phrases handled locally after the cue. Normalised to lowercase words.
from odessy.interaction.phrases import CANCEL_PHRASES, is_cancel

#: What a stopped turn is told, and what the user is told back. The wording matters:
#: a cancellation does not undo an action that already ran.
STOPPED_TEXT = "Stopped. Actions that already completed remain completed; remote completion may be unknown."

FrameSource = Callable[[], Awaitable[bytes]]
"""Reads one wake-sized frame. Raises on end of stream or device failure."""

Transcribe = Callable[[bytes], Awaitable[str]]
Speak = Callable[[str], Awaitable[None]]
StopSpeaking = Callable[[], None]
RunTurn = Callable[[str, asyncio.Event, ConversationContext], Awaitable[str]]


@dataclass(frozen=True)
class SessionOptions:
    interaction: Interaction = "wake"
    blank_retries: int = 2
    followup_ms: int = 10_000
    cue: bool = True
    settling_ms: int = 300

    def __post_init__(self) -> None:
        if self.interaction not in ("wake", "conversation"):
            raise ValueError("interaction must be 'wake' or 'conversation'")
        if self.blank_retries < 0:
            raise ValueError("blank_retries must not be negative")


@dataclass
class VoiceSession:
    """Drive wake → capture → turn → reply, with interruption."""

    frames: FrameSource
    wake: WakeDetector
    gate: WakeGate
    vad: SpeechDetector
    transcribe: Transcribe
    speak: Speak
    stop_speaking: StopSpeaking
    run_turn: RunTurn
    classify_blank: Callable[[str], str | None]
    options: SessionOptions = field(default_factory=SessionOptions)
    segmenter: UtteranceSegmenter = field(default_factory=UtteranceSegmenter)
    memory: ConversationMemory = field(default_factory=ConversationMemory)
    status: Callable[[str], None] = lambda message: None
    cue: Callable[[], Awaitable[None]] | None = None

    close_frames: Callable[[], None] | None = None
    #: Speak streamed model text while the turn runs, instead of after it.
    stream_speech: bool = False
    stop_turn: Callable | None = None
    #: Append completed turns to :attr:`memory`. A hybrid session supplies its own
    #: authoritative history, so it disables this and keeps the shared memory read
    #: only. The default preserves the standalone session's behaviour.
    remember_turns: bool = True
    #: Called when a fresh wake activation interrupts whatever is running. It lets a
    #: hybrid adapter stop the turn *it* owns (a typed one) without reaching into
    #: private state, and it is never suppressed by a typed turn.
    on_interrupt: Callable[[], None] | None = None
    #: Called with ``(stage, message)`` when audio fails. Stages are ``capture``,
    #: ``wake`` and ``stt``; the adapter decides what each failure disables.
    on_audio_error: Callable[[str, str], None] | None = None
    #: A standalone session ends when its device fails: the request cannot be heard,
    #: so the running turn is stopped and a pending prompt is cancelled. A hybrid
    #: session only loses its audio: the running turn keeps its model and tool work,
    #: and a pending prompt keeps its id, expiry and other channels.
    end_on_device_failure: bool = True

    state: SessionState = "LISTENING"
    error: str | None = None

    _stop: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _pending: str | None = field(default=None, repr=False)
    _pending_ready: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _vad_frames: FrameBuffer = field(init=False, repr=False)
    _frames_since_wake: int | None = None
    _followup_frames: int = 0
    _turn_active: bool = False
    _turn_id: int = 0
    _blank_attempts: int = 0
    _closing: bool = False
    _worker: asyncio.Task[None] | None = field(default=None, repr=False)

    _reader: asyncio.Task[None] | None = field(default=None, repr=False)
    _frames_ready: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _transcription: asyncio.Task[None] | None = field(default=None, repr=False)
    _capture_pending: tuple[bytes, int] | None = field(default=None, repr=False)
    _interaction: dict | None = field(default=None, repr=False)
    _interaction_broker: object | None = field(default=None, repr=False)
    _interaction_done: asyncio.Future | None = field(default=None, repr=False)
    _audio_generation: int = 0
    _captured_index: int = -1
    _admit_after_index: int = -1
    _interaction_attempts: int = 0
    _interaction_retry_task: asyncio.Task | None = field(default=None, repr=False)
    _input_suspended: bool = False
    _admission_enabled: bool = True
    _preserve_turn: bool = False
    _resume_signal: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    start_recording: bool = False
    on_reply_complete: Callable[[], None] | None = None

    def interaction_handler(self, broker):
        async def handle(entry):
            await self.handle_interaction(entry, broker)
        return handle

    async def handle_interaction(self, entry: dict, broker) -> None:
        from odessy.voice.interaction import interaction_prompt, voice_allowed

        self._interaction = entry
        self._interaction_broker = broker
        self._interaction_done = asyncio.get_running_loop().create_future()
        self._interaction_attempts = 0
        turn_id = self._turn_id
        try:
            await self._interaction_cue(interaction_prompt(entry))
            if self._closing or turn_id != self._turn_id:
                return
            broker.mark_ready(entry['id'])
            if not voice_allowed(entry):
                self.status('This action requires keyboard or authenticated UI confirmation.')
                self.state = 'THINKING'
            await self._interaction_done
        finally:
            if self._interaction is entry:
                if self._interaction_retry_task is not None:
                    self._interaction_retry_task.cancel()
                    await asyncio.gather(self._interaction_retry_task, return_exceptions=True)
                    self._interaction_retry_task = None
                if self.state == 'INTERACTION_PROMPT':
                    with contextlib.suppress(Exception):
                        self.stop_speaking()
                self._interaction = None
                self._interaction_broker = None
                self._interaction_done = None
                self._audio_generation += 1
                self._admit_after_index = self._captured_index
                self._capture_pending = None
                if not self._closing and turn_id == self._turn_id:
                    self.state = 'THINKING'

    async def _interaction_cue(self, prompt: str) -> None:
        self._audio_generation += 1
        self._capture_pending = None
        self.state = 'INTERACTION_PROMPT'
        self.status(prompt)
        if self._interaction and self._interaction['kind'] == 'approval':
            from odessy.voice.interaction import interaction_details
            self.status(interaction_details(self._interaction))
            self.status('Approval phrase: approve this action')
        await self.speak(prompt)
        await asyncio.sleep(self.options.settling_ms / 1000)
        if self.options.cue and self.cue is not None:
            await self.cue()
        self._admit_after_index = self._captured_index
        self.vad.reset()
        self._vad_frames.reset()
        self.segmenter.begin()
        self.state = 'RECORDING'

    def _cancel_interaction(self, reason: str) -> None:
        if self._interaction_broker is not None:
            self._interaction_broker.cancel_pending(reason)
        if self._interaction_done is not None and not self._interaction_done.done():
            self._interaction_done.set_result(None)

    def __post_init__(self) -> None:
        self._vad_frames = FrameBuffer(self.vad.frame_bytes)
        # Keep the caller's timings; only the frame size is the session's to decide,
        # because it has to match the VAD's frame size.
        self.segmenter.config = replace(
            self.segmenter.config, frame_ms=_frame_ms(self.vad.frame_bytes)
        )

    # -- lifecycle ---------------------------------------------------------------

    async def run(self) -> None:
        """Listen until the source ends, a device fails, or ``aclose`` is called."""

        self._worker = asyncio.create_task(self._serve())
        try:
            if self.start_recording:
                await self._activate()
            await self._read_loop()
        finally:
            await self.aclose()
            if self._transcription is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await self._transcription
            if self._worker is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await self._worker
                self._worker = None
            if self.state != "ERROR":
                self.state = "STOPPING"

    async def aclose(self, *, preserve_turn: bool = False) -> None:
        self._preserve_turn = self._preserve_turn or preserve_turn
        if preserve_turn and self._interaction_done is not None and not self._interaction_done.done():
            self._interaction_done.set_result(None)
        self._closing = True
        if not self._preserve_turn:
            self._cancel_interaction('voice session closed')
        self._pending = None
        self._capture_pending = None
        if not self._preserve_turn:
            self._stop.set()
        self._pending_ready.set()
        self._frames_ready.set()
        # The consumer may be parked on a suspension; closing must always wake it.
        self._resume_signal.set()
        if self._reader is not None:
            self._reader.cancel()
        close, self.close_frames = self.close_frames, None
        if close is not None:
            await asyncio.to_thread(close)
        with contextlib.suppress(Exception):
            self.stop_speaking()

    # -- public seams for a hybrid (typed + voice) session -----------------------

    def set_input_enabled(self, enabled: bool) -> None:
        """Suspend or resume consuming frames, without closing the device.

        The microphone stream stays open: reopening hardware on a toggle is slow and
        can fail, and *one reader* is the whole design. While suspended, queued audio
        is discarded instead of scored, so speech captured for another purpose can
        never be admitted as a request.
        """

        self._input_suspended = not enabled
        if enabled:
            self._resume_signal.set()
        else:
            self._resume_signal.clear()

    def set_utterance_admission(self, enabled: bool) -> None:
        """Whether speech *without* a fresh wake phrase may open capture.

        The follow-up window is the only such path. A hybrid session closes it while
        a typed turn owns the foreground; a fresh wake phrase still activates.
        """

        self._admission_enabled = enabled
        if not enabled and self.state == "FOLLOWUP":
            self._enter_listening()

    def invalidate_capture(self) -> None:
        """Drop the recording in progress and any transcript still decoding.

        The in-flight transcription compares its generation on return, so a stale
        result is discarded rather than submitted as the user's next request.
        """

        self._audio_generation += 1
        self._capture_pending = None
        self._vad_frames.reset()
        self.segmenter.begin()
        self._blank_attempts = 0
        if self._interaction is None and self.state in ("RECORDING", "TRANSCRIBING"):
            self._enter_listening()

    # -- the reader: owns the device, never blocks on a turn ---------------------

    async def _read_loop(self) -> None:
        # Two seconds of audio; fail rather than silently splice an utterance.
        queue = FrameQueue(max_frames=25)
        ready = self._frames_ready

        async def capture() -> None:
            index = 0
            while not self._closing:
                try:
                    frame = await self.frames()
                    if not frame:
                        raise RuntimeError("microphone capture ended")
                    # A suspended consumer never reads: dropping here is what keeps a
                    # long typed turn from overflowing the bounded queue.
                    if not self._input_suspended:
                        queue.push(AudioFrame(pcm=frame, index=index))
                    self._captured_index = index
                    index += 1
                    if queue.overflowed:
                        if self._input_suspended:
                            queue.clear()
                        else:
                            self._fail("microphone capture queue overflow")
                            return
                except Exception as error:
                    self._fail(f"microphone capture failed: {error}")
                    return
                finally:
                    ready.set()
                await asyncio.sleep(0)

        reader = self._reader = asyncio.create_task(capture())
        try:
            while not self._closing:
                if self._input_suspended:
                    # Typed input owns the foreground: the device keeps running, but
                    # nothing heard while suspended may become a request.
                    queue.clear()
                    ready.clear()
                    self._resume_signal.clear()
                    await self._resume_signal.wait()
                    if self._closing:
                        return
                    queue.clear()
                    self.invalidate_capture()
                    self.wake.reset()
                    self.vad.reset()
                    self._frames_since_wake = None
                    continue
                await ready.wait()
                ready.clear()
                for frame in queue.pop_all():
                    if self._closing:
                        return
                    if self._input_suspended:
                        # A suspension during a batch voids the rest of it.
                        queue.clear()
                        break
                    if frame.index <= self._admit_after_index:
                        continue
                    activation = self._turn_id
                    try:
                        await self._handle_frame(frame.pcm)
                    except Exception as error:
                        self._fail(f"voice session error: {error}", "wake")
                        return
                    if activation != self._turn_id:
                        # Discard audio captured before/during the ready cue.
                        queue.clear()
                        break
                    await asyncio.sleep(0)
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader
            self._reader = None

    async def _handle_frame(self, frame: bytes) -> None:
        if self.state == 'INTERACTION_PROMPT':
            return
        if self._interaction is not None and self.state == 'RECORDING':
            score = self.wake.score(frame)
            if self.gate.accepts(score, frames_since_activation=self._frames_since_wake):
                await self._activate()
                return
        if self.state == "RECORDING":
            await self._feed_segmenter(frame)
            return

        if self.state == "FOLLOWUP":
            if await self._followup_speech(frame):
                return

        # Wake scoring runs in every state except RECORDING (returned above), which is
        # what lets the phrase interrupt processing and playback.
        score = self.wake.score(frame)
        accepted = self.gate.accepts(score, frames_since_activation=self._frames_since_wake)
        self._frames_since_wake = (
            0 if accepted else (None if self._frames_since_wake is None else self._frames_since_wake + 1)
        )
        if accepted:
            await self._activate()

    # -- activation and capture --------------------------------------------------

    async def _activate(self) -> None:
        """Wake phrase heard: interrupt, cue, then open one command window."""

        self._turn_id += 1
        self._audio_generation += 1
        self._cancel_interaction('interrupted by wake phrase')
        self._pending = None
        self._pending_ready.clear()
        self._capture_pending = None
        interrupting = self._turn_active or self.state == "SPEAKING"
        self._stop.set()
        with contextlib.suppress(Exception):
            self.stop_speaking()
        # A fresh wake phrase is the established interruption, whoever owns the
        # foreground turn: the adapter stops a typed turn here.
        if self.on_interrupt is not None:
            with contextlib.suppress(Exception):
                self.on_interrupt()
        if interrupting:
            self.status("Interrupted")

        # Model buffers hold audio from before the cue; without this the cue itself
        # and the tail of the previous utterance are scored as the next command.
        self.wake.reset()
        self.vad.reset()
        self._vad_frames.reset()

        self.state = "READY"
        if self.options.cue and self.cue is not None:
            await self.cue()
        if self._closing:
            return
        # Capture starts after the cue returns: the contract is that the user speaks
        # after the cue, so cue audio is never part of the utterance.
        self.segmenter.begin()
        self._blank_attempts = 0
        self.state = "RECORDING"

    async def _feed_segmenter(self, frame: bytes) -> None:
        for vad_frame in self._vad_frames.push(frame):
            outcome = self.segmenter.push(vad_frame, self.vad.is_speech(vad_frame))
            if outcome == "ended":
                await self._finish_capture()
                return
            if outcome == "no_speech":
                self.status("No speech detected")
                if self._interaction is not None:
                    # Prompt playback must never hold up the sole frame consumer.
                    self.state = 'INTERACTION_PROMPT'
                    self._interaction_retry_task = asyncio.create_task(self._interaction_retry())
                    return
                self._enter_listening()
                return

    async def _finish_capture(self) -> None:
        self._capture_pending = (self.segmenter.utterance, self._turn_id)
        self.state = "TRANSCRIBING"
        self.status("Transcribing speech")
        if self._transcription is None or self._transcription.done():
            self._transcription = asyncio.create_task(self._transcribe_pending())

    async def _transcribe_pending(self) -> None:
        # Serialize STT, retaining at most one replacement recording.
        while self._capture_pending is not None and not self._closing:
            audio, turn_id = self._capture_pending
            generation = self._audio_generation
            self._capture_pending = None
            try:
                transcript = await self.transcribe(audio)
            except Exception as error:
                if generation != self._audio_generation:
                    continue
                if turn_id == self._turn_id and not self._closing:
                    self.status(f"Transcription failed: {error}")
                    self._report_audio_error("stt", f"transcription failed: {error}")
                    if self._interaction is not None and generation == self._audio_generation:
                        await self._interaction_retry()
                        continue
                    self._enter_listening()
                continue
            if generation != self._audio_generation:
                continue
            await self._submit(transcript, turn_id)

    async def _submit(self, transcript: str, turn_id: int) -> None:
        """Decide locally, or queue exactly one pending request."""

        if turn_id != self._turn_id or self._closing:
            return
        text = " ".join(transcript.split())
        if _is_cancel(text):
            self._cancel_interaction('stopped by voice')
            from odessy.voice.interaction import schedule_stop
            schedule_stop(self.stop_turn)
            self._pending = None
            self._pending_ready.clear()
            self._stop.set()
            # Handled without the model: a cancellation instruction is not a request
            # that needs a tool-call decision.
            self.status(STOPPED_TEXT)
            self._enter_listening()
            return

        if self._interaction is not None:
            from odessy.voice.interaction import resolve_spoken_answer
            if resolve_spoken_answer(self._interaction, self._interaction_broker, text):
                if self._interaction_done is not None and not self._interaction_done.done():
                    self._interaction_done.set_result(None)
                self.state = 'THINKING'
            else:
                await self._interaction_retry()
            return

        blank = self.classify_blank(text)
        if blank is not None:
            self._blank_attempts += 1
            if self._blank_attempts <= self.options.blank_retries:
                # Re-record rather than resubmit: a new recording is a new request,
                # and the blank one was never sent to the model.
                total = self.options.blank_retries + 1
                self.status(
                    f"No usable speech ({blank}); recording again "
                    f"({self._blank_attempts + 1}/{total})"
                )
                self._vad_frames.reset()
                self.segmenter.begin()
                self.state = "RECORDING"
                return
            self.status(f"No usable speech ({blank}); nothing was sent to the model")
            self._enter_listening()
            return

        self._blank_attempts = 0
        self._pending = text
        self._pending_ready.set()
        self.state = "THINKING"

    # -- the worker: one turn at a time -----------------------------------------

    async def _interaction_retry(self) -> None:
        from odessy.voice.interaction import voice_allowed
        if self._interaction is None:
            return
        if not voice_allowed(self._interaction):
            self.status('This action requires keyboard or authenticated UI confirmation.')
            self.state = 'THINKING'
            return
        self._interaction_attempts += 1
        if self._interaction_attempts <= 2:
            await self._interaction_cue('I could not use that response. Please answer the pending prompt after the cue.')
        else:
            self.status('Pending: no usable voice response; use the terminal or interaction API.')
            self.state = 'THINKING'

    async def _serve(self) -> None:
        while True:
            await self._pending_ready.wait()
            text, self._pending = self._pending, None
            self._pending_ready.clear()
            if self._closing:
                return
            if text is None:
                continue
            await self._run(text)

    async def _run(self, text: str) -> None:
        turn_id = self._turn_id
        self._stop = asyncio.Event()
        self._turn_active = True
        self.state = "THINKING"
        self.status("Model is thinking")
        stream, speaker, finish = self._start_streaming(turn_id) if self.stream_speech else (None, None, None)
        try:
            with speech_scope(stream):
                answer = await self.run_turn(text, self._stop, self.memory.context())
        except asyncio.CancelledError:
            if speaker is not None:
                speaker.cancel()
            raise
        except Exception as error:  # noqa: BLE001 - reported; the session continues
            self._turn_active = False
            if finish is not None:
                await finish()
            self.status(f"Turn failed: {error}")
            if turn_id == self._turn_id and not self._closing:
                self._enter_listening()
            return
        self._turn_active = False
        if finish is not None:
            if stream is not None and stream.emitted:
                self.state = "SPEAKING"
            await finish()

        stopped = self._stop.is_set()
        if stopped:
            answer = STOPPED_TEXT
        elif turn_id == self._turn_id and self.remember_turns:
            self.memory.add(text, answer)

        if self._closing:
            return
        if turn_id != self._turn_id:
            # A newer activation superseded this turn: its answer is stale, and
            # speaking it would talk over whatever the user is now saying.
            return

        if stream is not None and not stopped and stream.emitted and same_text(stream.last_text, answer):
            self._after_reply(turn_id)
            return
        await self._say(answer, turn_id)

    def _start_streaming(self, turn_id: int):
        """A speech stream for this turn, the task that speaks it, and how to finish it.

        The model thread emits sentences onto this loop; one task speaks them in order.
        A sentence whose turn was stopped or superseded is dropped, not spoken.
        """

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        stop = self._stop
        delivered = 0

        def deliver(sentence: str) -> None:
            nonlocal delivered
            delivered += 1
            queue.put_nowait(sentence)

        def emit(sentence: str) -> None:
            with contextlib.suppress(RuntimeError):  # The loop closed under a late thread.
                loop.call_soon_threadsafe(deliver, sentence)

        stream = SpeechStream(emit)

        async def speak_sentences() -> None:
            while (sentence := await queue.get()) is not None:
                if turn_id != self._turn_id or self._closing or stop.is_set():
                    continue
                self.status("Speaking")
                try:
                    await self.speak(sentence)
                except Exception as error:  # noqa: BLE001 - a failed sentence is not fatal
                    self.status(f"Speech output failed: {error}")

        speaker = asyncio.create_task(speak_sentences())

        async def finish() -> None:
            # A sentence the model thread emitted can still be on its way to this loop
            # when the turn returns; the end marker must not overtake it. The count
            # is final once the generation thread is done, and the wait is bounded in
            # case a stopped generation is still running.
            deadline = loop.time() + 1.0
            while delivered < stream.emitted and loop.time() < deadline:
                await asyncio.sleep(0.001)
            queue.put_nowait(None)
            await speaker

        return stream, speaker, finish

    async def _say(self, text: str, turn_id: int) -> None:
        if not text.strip():
            self._enter_listening()
            return
        self.state = "SPEAKING"
        self.status("Speaking")
        try:
            await self.speak(text)
        except Exception as error:  # noqa: BLE001 - a failed reply is not fatal
            self.status(f"Speech output failed: {error}")
        self._after_reply(turn_id)

    def _after_reply(self, turn_id: int) -> None:
        if turn_id != self._turn_id or self._closing:
            return
        if self.on_reply_complete is not None:
            self.on_reply_complete()
        if self.options.interaction == "conversation":
            self.state = "FOLLOWUP"
            self._followup_frames = self.options.followup_ms // WAKE_FRAME_MS
            self.status("Listening for a follow-up")
        else:
            self._enter_listening()

    # -- follow-up window --------------------------------------------------------

    async def _followup_speech(self, frame: bytes) -> bool:
        """Within the follow-up window, speech opens capture without a wake phrase."""

        if not self._admission_enabled:
            self._enter_listening()
            return False
        if self._followup_frames <= 0:
            self.status("Follow-up window closed")
            self._enter_listening()
            return False
        self._followup_frames -= 1
        for vad_frame in self._vad_frames.push(frame):
            if self.vad.is_speech(vad_frame):
                self.vad.reset()
                self._vad_frames.reset()
                self.segmenter.begin()
                self.state = "RECORDING"
                return True
        return False

    # -- state helpers -----------------------------------------------------------

    def _enter_listening(self) -> None:
        self.wake.reset()
        self.vad.reset()
        self._vad_frames.reset()
        self._followup_frames = 0
        self._blank_attempts = 0
        self.state = "LISTENING"

    def _report_audio_error(self, stage: str, message: str) -> None:
        if self.on_audio_error is not None:
            with contextlib.suppress(Exception):
                self.on_audio_error(stage, message)

    def _fail(self, message: str, stage: str = "capture") -> None:
        self._report_audio_error(stage, message)
        if self.end_on_device_failure:
            # Losing the device ends a standalone session, so its prompt goes with it.
            # A hybrid one keeps the pending request for its other channels.
            self._cancel_interaction(message)
            self._stop.set()
        else:
            # The session is winding down, but the turn it is running is not: closing
            # must not read to the model as a stop.
            self._preserve_turn = True
        self._pending = None
        self._capture_pending = None
        self.error = message
        self.state = "ERROR"
        self.status(message)
        self._closing = True
        self._pending_ready.set()
        with contextlib.suppress(Exception):
            self.stop_speaking()


def _is_cancel(text: str) -> bool:
    return is_cancel(text)


def _frame_ms(frame_bytes: int) -> int:
    from odessy.voice.frames import BYTES_PER_MS

    return max(1, frame_bytes // BYTES_PER_MS)


def pcm_to_wav(pcm: bytes, *, sample_rate: int = 16_000) -> bytes:
    """Wrap raw 16-bit mono PCM in a WAV container for the existing STT adapter."""

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def write_cue_wav(path: "Path", *, milliseconds: int = 150, frequency: int = 880) -> None:
    """Write the short ready tone the user speaks after.

    A cue rather than a spoken word: it must be recognisably not speech, so the
    detector and the transcriber never see it as part of the request.
    """

    import math
    import struct
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = int(16_000 * milliseconds / 1000)
    frames = bytearray()
    for index in range(samples):
        # Linear fade in and out so the tone does not click.
        fade = min(1.0, index / 400, (samples - index) / 400)
        value = int(6000 * fade * math.sin(2 * math.pi * frequency * index / 16_000))
        frames.extend(struct.pack("<h", value))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(bytes(frames))


async def iter_frames(stream: AsyncIterator[bytes], size: int = WAKE_FRAME_BYTES) -> AsyncIterator[bytes]:
    """Re-cut an async byte stream into fixed frames. Used by the CLI adapter."""

    pending = bytearray()
    async for chunk in stream:
        pending.extend(chunk)
        while len(pending) >= size:
            yield bytes(pending[:size])
            del pending[:size]
