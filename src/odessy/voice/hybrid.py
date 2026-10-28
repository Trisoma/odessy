"""Recoverable audio for the hybrid (typed + voice) session.

The console owns typed input, history, approvals and the foreground gate. This module
owns *only* the audio half: one microphone reader, wake detection, capture, speech
recognition, synthesis and playback. It never calls a model, never reads stdin and
never writes conversation history.

The rule that shapes it: **audio failures degrade audio, not the session**.

* A capture, wake or speech-recognition failure disables voice *input* and reports the
  stage. Typed turns keep working.
* A synthesis or playback failure disables speech *output*, once. The answer that was
  already computed is Codex's to display; nothing here replays a turn.
* Nothing is retried in a loop. Reopening hardware happens on an explicit
  ``set_input_enabled(True)`` (``/voice``) or ``set_output_enabled(True)``.

A typed request takes the foreground through :meth:`set_typed_turn`: partial speech is
void, a transcript still decoding is discarded, and speech without a wake phrase is
not admitted. A *fresh wake phrase* is still honoured, because that is the session's
established interruption: it stops the typed turn and opens a new capture window.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import uuid
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Awaitable, Callable, cast

from odessy.voice.capture import (
    CommandAudioStream,
    CommandPlayer,
    player_available,
    recorder_available,
)
from odessy.voice.config import VoiceConfig
from odessy.voice.frames import WAKE_FRAME_BYTES
from odessy.voice.interaction import schedule_stop
from odessy.voice.memory import ConversationMemory
from odessy.voice.segmentation import SegmenterConfig, UtteranceSegmenter
from odessy.voice.session import (
    SessionOptions,
    VoiceSession,
    pcm_to_wav,
    write_cue_wav,
)
from odessy.voice.stt import WhisperCppSTT, blank_transcript_reason
from odessy.voice.tts import KokoroTTS
from odessy.voice.providers import create_stt, create_tts, close_providers, cancel_provider
from odessy.voice.vad import SileroSpeechDetector
from odessy.voice.wake import OpenWakeWordDetector, WakeGate

#: Audio stages reported to the console. The first three disable voice input, the
#: last two disable speech output.
CAPTURE = "capture"
WAKE = "wake"
STT = "stt"
SYNTHESIS = "synthesis"
PLAYBACK = "playback"

INPUT_STAGES = frozenset({CAPTURE, WAKE, STT})

SubmitVoice = Callable[[str, asyncio.Event, Any], Awaitable[str]]


class AudioUnavailable(RuntimeError):
    """Voice cannot serve this request, and another channel still can.

    Raised from :meth:`HybridAudioAdapter.handle_interaction` *before* the broker is
    touched, so a terminal or API answer keeps the same request, the same expiry and
    the first-valid-wins outcome. It is never raised for an audio failure that the
    user is only being told about.
    """


def _no_audio_error(stage: str, message: str) -> None:
    return None


def _no_status(message: str) -> None:
    return None


@dataclass
class HybridAudioAdapter:
    """Own the microphone, the transcriber and the speaker of a hybrid session."""

    config: VoiceConfig
    #: Runs one completed voice request through the console's foreground gate. The
    #: returned string is the final answer to speak; display is not this module's job.
    submit_voice: SubmitVoice
    stop_turn: Callable[[], Any] | None = None
    report_audio_error: Callable[[str, str], None] = _no_audio_error
    report_status: Callable[[str], None] = _no_status
    #: The session's shared history. Read only: the adapter passes
    #: ``remember_turns=False`` so nothing is appended here.
    memory: ConversationMemory = field(default_factory=ConversationMemory)
    #: The existing interaction broker. Required to serve a pending prompt by voice.
    interactions: object | None = None
    listen: bool = True
    on_reply_complete: Callable[[], None] | None = None

    # Injectable components, so every failure can be reproduced without a device.
    stream_factory: Callable[[], Any] | None = None
    player_factory: Callable[[], Any] | None = None
    stt_factory: Callable[[], Any] | None = None
    tts_factory: Callable[[], Any] | None = None
    wake_factory: Callable[[], Any] | None = None
    vad_factory: Callable[[], Any] | None = None
    #: Bound on waiting for a blocking device call that cancellation cannot interrupt.
    drain_timeout: float = 5.0

    _stream: Any = field(default=None, init=False, repr=False)
    _stream_started: bool = field(default=False, init=False, repr=False)
    _player: Any = field(default=None, init=False, repr=False)
    _stt: Any = field(default=None, init=False, repr=False)
    _tts: Any = field(default=None, init=False, repr=False)
    _wake: Any = field(default=None, init=False, repr=False)
    _vad: Any = field(default=None, init=False, repr=False)
    _cue_path: Path | None = field(default=None, init=False, repr=False)
    _session: VoiceSession | None = field(default=None, init=False, repr=False)
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _blocking: set[asyncio.Task[Any]] = field(default_factory=set, init=False, repr=False)
    _input_enabled: bool = field(default=True, init=False, repr=False)
    _output_enabled: bool = field(default=True, init=False, repr=False)
    _typed_turn: bool = field(default=False, init=False, repr=False)
    _input_failure: str | None = field(default=None, init=False, repr=False)
    _output_failure: str | None = field(default=None, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)
    _speech_epoch: int = field(default=0, init=False, repr=False)
    _closing: bool = field(default=False, init=False, repr=False)
    _initializing: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    # -- availability ------------------------------------------------------------

    @property
    def input_available(self) -> bool:
        """Whether a wake phrase and a spoken request can be served right now."""

        return (
            self._started
            and not self._closing
            and self._input_enabled
            and self._input_failure is None
            and self._session_alive()
        )

    @property
    def output_available(self) -> bool:
        """Whether speech can still be synthesised and played."""

        return (
            self._started
            and not self._closing
            and self._output_enabled
            and self._output_failure is None
        )

    @property
    def typed_turn(self) -> bool:
        """Whether a typed turn currently owns the foreground."""

        return self._typed_turn

    # -- lifecycle ---------------------------------------------------------------

    async def start(self) -> bool:
        """Open audio, reporting recoverable failures. Returns voice-input health.

        Missing or broken audio never raises: the session is meant to survive it and
        keep accepting typed requests. Configuration, model, policy and tool-server
        errors belong to the console and still fail explicitly there.
        """

        if self._closing:
            return False
        self._started = True
        self._input_enabled = True
        async with self._initializing:
            await self._prepare_output()
            return await self._open_input()

    async def set_input_enabled(self, enabled: bool) -> bool:
        """Suspend voice input, or explicitly resume it. Returns input availability.

        Enabling is the *only* path that reopens failed hardware, which is what keeps
        a failing microphone from becoming a retry loop.
        """

        if self._closing:
            return False
        if not enabled:
            self._input_enabled = False
            self.stop_speaking()
            if self._session is not None:
                self._session.set_input_enabled(False)
            return False
        self._input_enabled = True
        async with self._initializing:
            return await self._open_input()

    async def set_output_enabled(self, enabled: bool) -> bool:
        """Suppress or explicitly resume speech. Returns output availability."""

        if self._closing:
            return False
        self._output_enabled = enabled
        if not enabled:
            self.stop_speaking()
            return False
        async with self._initializing:
            await self._prepare_output()
        return self.output_available

    async def set_typed_turn(self, active: bool) -> None:
        """Mark a typed turn as the foreground owner, or release it.

        Acceptance voids partial speech and any transcript still decoding, stops
        obsolete playback, and closes the wake-less follow-up window. A fresh wake
        phrase is deliberately *not* suppressed.
        """

        active = bool(active)
        if active == self._typed_turn:
            return
        self._typed_turn = active
        if active:
            self.stop_speaking()
            if self._session is not None:
                self._session.invalidate_capture()
                self._session.set_utterance_admission(False)
            return
        if self._session is not None and self.input_available:
            self._session.set_utterance_admission(True)

    async def aclose(self, *, preserve_turn: bool = False) -> None:
        """Stop playback, close capture and drain owned blocking calls."""

        if self._closing:
            return
        self._closing = True
        self._input_enabled = False
        self._output_enabled = False
        cancel_provider(self._stt)
        cancel_provider(self._tts)
        await self._stop_session(preserve_turn=preserve_turn)
        if self._stream_started:
            self._close_stream()
        self.stop_speaking()
        await self._drain_blocking()
        self.stop_speaking()
        cleanup = asyncio.create_task(asyncio.to_thread(close_providers, self._stt, self._tts))
        await asyncio.wait({cleanup}, timeout=self.drain_timeout)
        self._started = False

    # -- speech ------------------------------------------------------------------

    def stop_speaking(self) -> None:
        """Stop playback now. Synchronous, matching the existing player seam."""

        self._speech_epoch += 1
        cancel_provider(self._tts)
        player = self._player
        if player is None:
            return
        with contextlib.suppress(Exception):
            player.stop()

    async def transcribe_pcm(self, pcm: bytes) -> str:
        """Explicit push-to-talk seam: no wake/VAD or background reader started."""
        if self._closing:raise AudioUnavailable('Audio adapter is closed')
        if self._stt is None:
            self._stt = await self._run_blocking(self._create_provider, create_stt, WhisperCppSTT)
        return await self._transcribe(pcm)

    def cancel_transcription(self):
        cancel_provider(self._stt)

    async def speak_once(self,text):
        """Explicit playback using the same owned providers and cancellation guard."""
        if self._closing:return
        self._speech_epoch += 1
        epoch=self._speech_epoch
        self._started=True
        self._output_enabled=True
        if await self._prepare_output() and epoch==self._speech_epoch and not self._closing:
            await self.speak(text)

    async def speak(self, text: str) -> None:
        """Synthesise and play one answer, or degrade speech output.

        A failure here is reported once and disables speech; it never raises, so a
        turn that already produced an answer still completes and is displayed.
        """

        if not text.strip() or not self.output_available:
            return
        tts, player = self._tts, self._player
        if tts is None or player is None:
            return
        generation = getattr(player, "generation", 0)
        path = self._audio_path("reply")
        try:
            try:
                result = await self._run_blocking(tts.synthesize, text, path)
            except Exception as error:  # noqa: BLE001 - reported, never propagated
                self._fail_output(SYNTHESIS, f"speech synthesis failed: {error}")
                return
            if result.status != "ok":
                self._fail_output(
                    SYNTHESIS, result.error or f"speech synthesis returned {result.status}"
                )
                return
            if not self.output_available:
                # A later turn, /text or close made this audio obsolete.
                return
            try:
                await self._run_blocking(player.play, path, generation=generation)
            except Exception as error:  # noqa: BLE001 - reported, never propagated
                self._fail_output(PLAYBACK, f"speech playback failed: {error}")
        finally:
            _unlink(path)

    async def _cue(self) -> None:
        """Play the ready tone. Its failure is a playback failure, not a lost turn."""

        player = self._player
        if player is None or not self.output_available:
            return
        path = self._cue_file()
        if path is None:
            return
        generation = getattr(player, "generation", 0)
        try:
            await self._run_blocking(player.play, path, generation=generation)
        except Exception as error:  # noqa: BLE001 - reported, never propagated
            # A ready cue is advisory: a tone that did not play does not by itself
            # prove the answer cannot be heard, and the answer's own playback is the
            # authority on that. Report it, and still try to speak the reply.
            self._report(PLAYBACK, f"the ready cue could not be played: {error}")

    # -- interactions ------------------------------------------------------------

    async def handle_interaction(self, entry: dict) -> None:
        """Serve one pending prompt by voice, through the injected broker.

        Raises :class:`AudioUnavailable` when voice cannot serve it, *before* touching
        the broker: no mark-ready, no approval, no substituted request and no change to
        the deadline. The console keeps keyboard and API resolution for the same id.
        """

        if self.interactions is None:
            raise AudioUnavailable("no interaction broker is configured for voice")
        if not self.input_available:
            raise AudioUnavailable(
                "voice input is unavailable; resolve this prompt on the keyboard"
            )
        if not self.output_available:
            raise AudioUnavailable(
                "speech output is unavailable; the prompt must be answered elsewhere"
            )
        session = self._session
        if session is None:
            raise AudioUnavailable("the voice session is not running")
        await session.handle_interaction(entry, self.interactions)

    # -- input construction ------------------------------------------------------

    async def _open_input(self) -> bool:
        """An explicit attempt to (re)open the microphone."""

        if self._input_failure is not None and self._session is not None and self._session._turn_active:
            self._status('Audio retry deferred until this turn completes; type /voice again afterward.')
            return False
        self._input_failure = None
        if self._closing or not self._input_enabled:
            return False
        if not await self._ensure_components():
            return False
        if self._closing or not self._input_enabled:
            return False
        if self._session is not None and not self._session_alive():
            await self._stop_session()
        if self._session is None:
            if not await self._open_stream():
                return False
            if self._closing or not self._input_enabled:
                self._close_stream()
                return False
            self._start_session()
            if self._typed_turn:
                self._session.set_utterance_admission(False)
        else:
            self._session.set_input_enabled(True)
        return self.input_available

    async def _ensure_components(self) -> bool:
        if self._stream is None:
            if self.stream_factory is not None:
                self._stream = await self._run_blocking(self.stream_factory)
            elif self._native_audio():
                from .native_audio import recorder
                self._stream = recorder(self.config)
            elif not recorder_available(self.config.audio_recorder):
                self._fail_input(
                    CAPTURE,
                    "microphone capture is unavailable: "
                    f"{self.config.audio_recorder} was not found on PATH",
                )
                return False
            else:
                from .native_audio import recorder
                self._stream = recorder(self.config,command_factory=CommandAudioStream)
        if self._wake is None:
            if not self.listen:
                class NoWake:
                    def score(self, frame):
                        return 0.0
                    def reset(self):
                        pass
                self._wake = NoWake()
            elif self.wake_factory is not None:
                self._wake = await self._run_blocking(self.wake_factory)
            else:
                model = Path(self.config.wake_model)
                if not model.is_file():
                    self._fail_input(
                        WAKE,
                        f"wake word model is missing: {model}; "
                        "run `odessy voice --setup-wake`",
                    )
                    return False
                try:
                    self._wake = await self._run_blocking(OpenWakeWordDetector, str(model))
                except Exception as error:  # noqa: BLE001 - optional dependency
                    self._fail_input(WAKE, f"wake word detection is unavailable: {error}")
                    return False
        if self._vad is None:
            try:
                self._vad = (
                    await self._run_blocking(self.vad_factory)
                    if self.vad_factory is not None
                    else await self._run_blocking(SileroSpeechDetector)
                )
            except Exception as error:  # noqa: BLE001 - optional dependency
                self._fail_input(CAPTURE, f"speech detection could not start: {error}")
                return False
        if self._stt is None:
            if self.stt_factory is not None:
                self._stt = await self._run_blocking(self.stt_factory)
            else:
                reason = _recognition_missing(self.config)
                if reason is not None:
                    self._fail_input(STT, reason)
                    return False
                self._stt = await self._run_blocking(self._create_provider, create_stt, WhisperCppSTT)
        return True

    async def _open_stream(self) -> bool:
        if self._stream is None:
            self._fail_input(CAPTURE, "microphone capture is not configured")
            return False
        if self._stream_started:
            return True
        try:
            def open_stream():
                self._stream.start()
                if self._closing:
                    self._stream.close()
            await self._run_blocking(open_stream)
        except Exception as error:  # noqa: BLE001 - reported, never propagated
            self._fail_input(CAPTURE, f"microphone capture could not start: {error}")
            return False
        self._stream_started = True
        return True

    def _start_session(self) -> None:
        config = self.config
        stream = self._stream

        async def next_frame() -> bytes:
            return await asyncio.to_thread(stream.read, WAKE_FRAME_BYTES)

        session = VoiceSession(
            frames=next_frame,
            close_frames=self._close_stream,
            wake=self._wake,
            gate=WakeGate(threshold=config.wake_threshold),
            vad=self._vad,
            transcribe=self._transcribe,
            speak=self.speak,
            stop_speaking=self.stop_speaking,
            stop_turn=self.stop_turn,
            run_turn=self._run_turn,
            classify_blank=blank_transcript_reason,
            segmenter=UtteranceSegmenter(
                SegmenterConfig(max_utterance_ms=config.max_utterance_seconds * 1000)
            ),
            options=SessionOptions(
                interaction=cast(Any, config.interaction),
                settling_ms=config.settling_ms,
                blank_retries=config.blank_retries,
                followup_ms=config.followup_seconds * 1000,
            ),
            memory=self.memory,
            status=self._status,
            cue=self._cue,
            stream_speech=config.stream_speech,
            # The controller owns history in a hybrid session: read, never append.
            remember_turns=False,
            # Losing the microphone must not cancel a running turn or discard a
            # pending approval: other channels still serve both.
            end_on_device_failure=False,
            on_interrupt=self._on_interrupt,
            on_audio_error=self._on_audio_error,
            start_recording=not self.listen and not self._typed_turn,
            on_reply_complete=self.on_reply_complete,
        )
        self._session = session
        self._task = asyncio.create_task(session.run(), name="odessy-hybrid-voice")

    def _close_stream(self) -> None:
        stream, self._stream_started = self._stream, False
        if stream is None:
            return
        with contextlib.suppress(Exception):
            stream.close()

    async def _stop_session(self, *, preserve_turn: bool = False) -> None:
        session, self._session = self._session, None
        task, self._task = self._task, None
        if session is not None:
            with contextlib.suppress(Exception):
                await session.aclose(preserve_turn=preserve_turn)
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _session_alive(self) -> bool:
        task = self._task
        return self._session is not None and task is not None and not task.done()

    # -- the session's callbacks -------------------------------------------------

    async def _run_turn(self, text: str, stop: asyncio.Event, context: Any) -> str:
        # One controller, one admission path: the console decides whether this voice
        # request may run now.
        return await self.submit_voice(text, stop, context)

    async def _transcribe(self, pcm: bytes) -> str:
        stt = self._stt
        if stt is None:
            raise RuntimeError("speech recognition is not available")
        path = self._audio_path("utterance")
        path.write_bytes(pcm_to_wav(pcm))
        try:
            result = await self._run_blocking(stt.transcribe, path)
        finally:
            _unlink(path)
        if result.status == "empty":
            return ""
        if result.status != "ok":
            raise RuntimeError(
                result.error or f"speech recognition returned {result.status}"
            )
        return result.text

    def _status(self, message: str) -> None:
        with contextlib.suppress(Exception):
            self.report_status(message)

    def _on_interrupt(self) -> None:
        """A fresh wake phrase was heard: the established interruption still applies."""

        if self._session is not None:
            self._session.set_utterance_admission(True)
        if not self._typed_turn:
            return
        self._typed_turn = False
        self.stop_speaking()
        self._status("Interrupted")
        # The typed turn belongs to the controller; only its stop callback can end it.
        schedule_stop(self.stop_turn)

    def _on_audio_error(self, stage: str, message: str) -> None:
        if stage in INPUT_STAGES:
            self._fail_input(stage, message)
        else:
            self._fail_output(stage, message)

    def _fail_input(self, stage: str, message: str) -> None:
        self._input_failure = message
        self._report(stage, message)
        self.stop_speaking()
        if self._session is not None:
            # No busy retry: the microphone is not consumed until an explicit resume.
            self._session.set_input_enabled(False)

    def _fail_output(self, stage: str, message: str) -> None:
        self._output_failure = message
        self.stop_speaking()
        self._report(stage, message)

    def _report(self, stage: str, message: str) -> None:
        with contextlib.suppress(Exception):
            self.report_audio_error(stage, message)

    # -- output construction and owned blocking work -----------------------------

    async def _prepare_output(self) -> bool:
        if not self._output_enabled:
            return False
        if self._player is None:
            if self.player_factory is not None:
                self._player = await self._run_blocking(self.player_factory)
            elif self._native_audio():
                from .native_audio import player
                self._player = player(self.config)
            elif not player_available(self.config.audio_player):
                self._fail_output(
                    PLAYBACK,
                    "speech playback is unavailable: "
                    f"{self.config.audio_player} was not found on PATH",
                )
                return False
            else:
                self._player = CommandPlayer(
                    player=self.config.audio_player,
                    output_device=self.config.audio_output_device,
                )
        if self._tts is None:
            if self.tts_factory is not None:
                self._tts = await self._run_blocking(self.tts_factory)
            else:
                reason = _synthesis_missing(self.config)
                if reason is not None:
                    self._fail_output(SYNTHESIS, reason)
                    return False
                self._tts = await self._run_blocking(self._create_provider, create_tts, KokoroTTS)
        self._output_failure = None
        return True

    def _native_audio(self):
        from .native_audio import backend_name
        return backend_name(self.config.audio_backend)=='native'

    def _create_provider(self, factory, builtin):
        provider = factory(self.config, builtin_factory=builtin)
        if self._closing:
            close_providers(provider)
            raise RuntimeError('audio closed during provider initialization')
        return provider

    async def _run_blocking(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run a device call off the loop, shield it, and track it for draining.

        A cancelled ``to_thread`` keeps running: the thread still owns the device (or
        the temporary file the device is reading), so cleanup waits for it rather than
        releasing the resource underneath it.
        """

        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        self._blocking.add(task)

        def finished(done: asyncio.Task[Any]) -> None:
            self._blocking.discard(done)
            if not done.cancelled():
                done.exception()  # retrieved, so a failed drain is not a warning

        task.add_done_callback(finished)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancel_provider(getattr(function, '__self__', None))
            raise

    async def _drain_blocking(self) -> None:
        pending = [task for task in self._blocking if not task.done()]
        if pending:
            await asyncio.wait(pending, timeout=self.drain_timeout)

    # -- files -------------------------------------------------------------------

    def _audio_path(self, kind: str) -> Path:
        root = Path(self.config.tmp_dir)
        root.mkdir(parents=True, exist_ok=True)
        return root / f"odessy-{kind}-{uuid.uuid4().hex}.wav"

    def _cue_file(self) -> Path | None:
        if self._cue_path is not None:
            return self._cue_path
        path = Path(self.config.tmp_dir) / "odessy-cue.wav"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                write_cue_wav(path)
        except OSError:
            # A cue that cannot be written is cosmetic; it must not disable speech.
            return None
        self._cue_path = path
        return path


def _synthesis_missing(config: VoiceConfig) -> str | None:
    """Why speech synthesis cannot work, or ``None``. Feature-aware, not fatal."""

    if config.tts_provider != "kokoro":
        return None
    for label, value in (
        ("model", config.kokoro_model),
        ("voice data", config.kokoro_voices),
    ):
        path = Path(value)
        # A bare name is resolved by the engine against its own search path; only an
        # explicit path can be checked here without inventing a location.
        if path.is_absolute() and not path.exists():
            return f"speech synthesis {label} is missing: {path}"
    for module in ("onnxruntime", "soundfile"):
        if find_spec(module) is None:
            return f"speech synthesis needs {module}: pip install -e '.[voice]'"
    return None


def _recognition_missing(config: VoiceConfig) -> str | None:
    if config.stt_provider != "whisper.cpp":
        return None
    if shutil.which(config.whisper_bin) is None:
        return (
            "speech recognition is unavailable: "
            f"{config.whisper_bin} was not found on PATH"
        )
    model = Path(config.whisper_model)
    if model.is_absolute() and not model.exists():
        return f"speech recognition model is missing: {model}"
    return None


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
