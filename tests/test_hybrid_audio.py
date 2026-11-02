"""Recoverable audio for the hybrid (typed + voice) session.

Every test drives real session logic — the same reader, segmenter, interaction and
interruption paths the device uses — with a fake microphone, transcriber, synthesiser
and player. Nothing here needs hardware, a model, or a live cloud account.

Frame contents are the script, as in ``test_wake_session``: a frame starting with
``0x01`` is speech, ``0x02`` is the wake phrase, ``0x00`` is silence.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from odessy.voice.capture import AudioStreamError
from odessy.voice.config import VoiceConfig
from odessy.voice.frames import VAD_FRAME_BYTES, WAKE_FRAME_BYTES
from odessy.voice.hybrid import AudioUnavailable, HybridAudioAdapter
from odessy.voice.memory import ConversationMemory
from odessy.voice.stt import STTResult
from odessy.voice.tts import TTSResult
from odessy.voice.wake import WakeGate

SILENCE = b"\x00" * WAKE_FRAME_BYTES
SPEECH = b"\x01" * WAKE_FRAME_BYTES
WAKE = b"\x02" * WAKE_FRAME_BYTES

#: Enough frames to fill the segmenter's bounds: 640 ms of speech, 1.6 s of silence.
SPEECH_FRAMES = [SPEECH] * 8
END_FRAMES = [SILENCE] * 20
UTTERANCE = [*SPEECH_FRAMES, *END_FRAMES]


async def wait_until(predicate, *, timeout: float = 5.0, message: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"timed out waiting for {message}")


async def settle(seconds: float = 0.12) -> None:
    """Let the reader consume frames that must *not* turn into a request."""

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        await asyncio.sleep(0.005)


async def record(rig: "Rig", frames: list[bytes] | None = None) -> None:
    """Speak one utterance inside the open window, the way a user does."""

    await wait_until(_in_state(rig, "RECORDING"), message="the recording window")
    rig.stream.feed(*(frames if frames is not None else UTTERANCE))


async def say(rig: "Rig") -> None:
    """One complete voice request: the wake phrase, then the utterance."""

    rig.stream.feed(WAKE)
    await record(rig)


class FakeStream:
    """One long-lived microphone. ``read`` blocks off the event loop."""

    def __init__(self, frames: list[bytes] = (), *, start_error: str | None = None,
                 fail_after: int | None = None) -> None:
        self._frames = deque(frames)
        self.start_error = start_error
        self.fail_after = fail_after
        self.starts = 0
        self.closes = 0
        self.reads = 0
        self._lock = threading.Lock()

    def start(self) -> "FakeStream":
        self.starts += 1
        if self.start_error is not None:
            raise AudioStreamError(self.start_error)
        return self

    def feed(self, *frames: bytes) -> None:
        with self._lock:
            self._frames.extend(frames)

    def read(self, size: int) -> bytes:
        with self._lock:
            self.reads += 1
            if self.fail_after is not None and self.reads > self.fail_after:
                raise AudioStreamError("the recorder stopped producing audio")
            if self._frames:
                return self._frames.popleft()
        # Silence keeps the open device realistic without burning the CPU.
        time.sleep(0.002)
        return SILENCE

    def close(self) -> None:
        self.closes += 1


class FakeWake:
    frame_bytes = WAKE_FRAME_BYTES

    def score(self, pcm: bytes) -> float:
        return 0.9 if pcm[0:1] == b"\x02" else 0.0

    def reset(self) -> None:
        pass


class FakeVad:
    frame_bytes = VAD_FRAME_BYTES

    def is_speech(self, pcm: bytes) -> bool:
        return pcm[0:1] == b"\x01"

    def reset(self) -> None:
        pass


class FakeSTT:
    def __init__(self, texts: list[str] | None = None, *, status: str = "ok",
                 error: str | None = None, gate: threading.Event | None = None,
                 default: str = "what time is it") -> None:
        self.texts = deque(texts or [])
        self.default = default
        self.status = status
        self.error = error
        self.gate = gate
        self.calls: list[Path] = []

    def transcribe(self, audio_path: Path) -> STTResult:
        self.calls.append(Path(audio_path))
        if self.gate is not None:
            self.gate.wait(10)
        if self.status != "ok":
            return STTResult(text="", status=self.status, error=self.error or "recogniser failed")
        text = self.texts.popleft() if self.texts else self.default
        if not text:
            return STTResult(text="", status="empty", error="no text")
        return STTResult(text=text, status="ok")


class FakeTTS:
    def __init__(self, *, status: str = "ok", error: str | None = None,
                 gate: threading.Event | None = None) -> None:
        self.status = status
        self.error = error
        self.gate = gate
        self.texts: list[str] = []

    def synthesize(self, text: str, output_path: Path) -> TTSResult:
        self.texts.append(text)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"RIFF")
        if self.gate is not None:
            self.gate.wait(10)
        if self.status != "ok":
            return TTSResult(output_path=Path(output_path), status=self.status,
                             error=self.error or "synthesis failed")
        return TTSResult(output_path=Path(output_path), status="ok")


class FakePlayer:
    def __init__(self, *, fail: str | None = None, gate: threading.Event | None = None) -> None:
        self.fail = fail
        self.gate = gate
        self.played: list[Path] = []
        self.stops = 0
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    def play(self, audio_path: Path, *, generation: int | None = None) -> None:
        if generation is not None and generation != self._generation:
            return
        if generation is None:
            self._generation += 1
        self.played.append(Path(audio_path))
        if self.gate is not None:
            self.gate.wait(10)
        if self.fail is not None:
            raise AudioStreamError(self.fail)

    def stop(self) -> None:
        self._generation += 1
        self.stops += 1


class FakeBroker:
    """The parts of the interaction broker the voice adapter is allowed to use."""

    def __init__(self, entry: dict) -> None:
        self.entry = entry
        self.ready: list[str] = []
        self.approvals: list[tuple[str, bool, str | None]] = []
        self.questions: list[tuple[str, str, str | None]] = []
        self.cancelled: list[str] = []

    def is_pending(self, request_id: str) -> bool:
        return request_id == self.entry["id"]

    def mark_ready(self, request_id: str) -> None:
        self.ready.append(request_id)

    def resolve_approval(self, request_id: str, approved: bool, channel: str | None = None) -> bool:
        self.approvals.append((request_id, approved, channel))
        return True

    def resolve_question(self, request_id: str, answer: str, channel: str | None = None) -> bool:
        self.questions.append((request_id, answer, channel))
        return True

    def cancel_pending(self, reason: str) -> None:
        self.cancelled.append(reason)


@dataclass
class Rig:
    adapter: HybridAudioAdapter
    stream: FakeStream | None
    stt: FakeSTT | None
    tts: FakeTTS | None
    player: FakePlayer | None
    submitted: list[str] = field(default_factory=list)
    stop_calls: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)

    async def close(self) -> None:
        await self.adapter.aclose()


def build(
    tmp_path: Path,
    *,
    frames: list[bytes] = (),
    stream: FakeStream | Any = None,
    stt: FakeSTT | Any = None,
    tts: FakeTTS | Any = None,
    player: FakePlayer | Any = None,
    broker: FakeBroker | None = None,
    interaction: str = "wake",
    submit: Any = None,
    **adapter_options: Any,
) -> Rig:
    """A rig wired to fakes; ``start()`` is the caller's decision.

    A ``*_factory=None`` override makes the adapter use its real component, which is
    how the missing-dependency tests reach the feature-aware startup checks.
    """

    config = _config_with(tmp_path, interaction=interaction)
    rig = Rig(adapter=None, stream=None, stt=None, tts=None, player=None)  # type: ignore[arg-type]

    async def submit_voice(text: str, stop: asyncio.Event, context: Any) -> str:
        rig.submitted.append(text)
        if submit is not None:
            return await submit(text, stop, context)
        return f"answer to {text}"

    def stop_turn() -> None:
        rig.stop_calls.append("stop")

    def report_audio_error(stage: str, message: str) -> None:
        rig.errors.append((stage, message))

    microphone = stream if stream is not None else FakeStream(frames)
    recogniser = stt if stt is not None else FakeSTT(["what time is it"])
    synthesiser = tts if tts is not None else FakeTTS()
    speaker = player if player is not None else FakePlayer()

    factories = {
        "stream_factory": lambda: microphone,
        "player_factory": lambda: speaker,
        "stt_factory": lambda: recogniser,
        "tts_factory": lambda: synthesiser,
        "wake_factory": FakeWake,
        "vad_factory": FakeVad,
    }
    factories.update(adapter_options)

    rig.adapter = HybridAudioAdapter(
        config=config,
        submit_voice=submit_voice,
        stop_turn=stop_turn,
        report_audio_error=report_audio_error,
        report_status=rig.statuses.append,
        memory=ConversationMemory(),
        interactions=broker,
        **factories,
    )
    rig.stream = microphone if isinstance(microphone, FakeStream) else None
    rig.stt = recogniser if isinstance(recogniser, FakeSTT) else None
    rig.tts = synthesiser if isinstance(synthesiser, FakeTTS) else None
    rig.player = speaker if isinstance(speaker, FakePlayer) else None
    return rig


# -- startup and degradation --------------------------------------------------


@pytest.mark.anyio
async def test_a_missing_microphone_leaves_typed_input_usable(tmp_path):
    # A recorder that is not on PATH is the common "no audio hardware" case.
    rig = build(tmp_path, stream_factory=None)
    rig.adapter.config = _config_with(tmp_path, audio_recorder="odessy-no-such-recorder")

    started = await rig.adapter.start()

    assert started is False
    assert rig.adapter.input_available is False
    assert rig.adapter.output_available is True
    assert [stage for stage, _ in rig.errors] == ["capture"]
    assert "odessy-no-such-recorder" in rig.errors[0][1]
    await rig.close()


@pytest.mark.anyio
async def test_a_missing_recogniser_is_reported_rather_than_raised(tmp_path):
    rig = build(tmp_path, stt_factory=None)
    rig.adapter.config = _config_with(tmp_path, whisper_bin="odessy-no-such-whisper")

    started = await rig.adapter.start()

    assert started is False
    assert rig.adapter.input_available is False
    assert rig.adapter.output_available is True
    assert [stage for stage, _ in rig.errors] == ["stt"]
    await rig.close()


@pytest.mark.anyio
async def test_missing_synthesis_assets_disable_only_speech(tmp_path):
    rig = build(tmp_path, tts_factory=None)
    rig.adapter.config = _config_with(
        tmp_path, kokoro_model=str(tmp_path / "absent-kokoro.onnx")
    )

    started = await rig.adapter.start()

    assert started is True
    assert rig.adapter.input_available is True
    assert rig.adapter.output_available is False
    assert [stage for stage, _ in rig.errors] == ["synthesis"]
    await rig.close()


@pytest.mark.anyio
async def test_explicit_resume_reopens_a_failed_microphone_once(tmp_path):
    stream = FakeStream(start_error="arecord: device busy")
    rig = build(tmp_path, stream=stream, stream_factory=lambda: stream)

    assert await rig.adapter.start() is False
    assert rig.adapter.input_available is False
    assert [stage for stage, _ in rig.errors] == ["capture"]

    # The hardware recovers; only an explicit resume may use it again.
    stream.start_error = None
    assert await rig.adapter.set_input_enabled(True) is True
    assert rig.adapter.input_available is True
    assert stream.starts == 2
    assert [stage for stage, _ in rig.errors] == ["capture"]

    # A repeated resume while healthy does not reopen anything.
    assert await rig.adapter.set_input_enabled(True) is True
    assert stream.starts == 2
    assert [stage for stage, _ in rig.errors] == ["capture"]
    await rig.close()


@pytest.mark.anyio
async def test_a_capture_failure_mid_session_disables_input_and_keeps_output(tmp_path):
    stream = FakeStream([WAKE, *SPEECH_FRAMES], fail_after=3)
    rig = build(tmp_path, stream=stream, stream_factory=lambda: stream)

    assert await rig.adapter.start() is True
    await wait_until(lambda: rig.errors, message="a capture failure report")
    await wait_until(lambda: not rig.adapter.input_available, message="input to be disabled")

    assert rig.errors[0][0] == "capture"
    assert rig.adapter.output_available is True
    assert rig.submitted == []
    await rig.close()


@pytest.mark.anyio
async def test_a_capture_failure_never_stops_the_running_turn(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    stopped: list[bool] = []

    async def slow_turn(text: str, stop: asyncio.Event, context: Any) -> str:
        entered.set()
        await release.wait()
        stopped.append(stop.is_set())
        return "the completed answer"

    stream = FakeStream()
    rig = build(tmp_path, stream=stream, stream_factory=lambda: stream, submit=slow_turn)

    assert await rig.adapter.start() is True
    await say(rig)
    await asyncio.wait_for(entered.wait(), 5)

    # The microphone dies while the model and its tools are still working.
    stream.fail_after = stream.reads
    await wait_until(lambda: not rig.adapter.input_available, message="input degradation")
    release.set()
    await wait_until(lambda: stopped, message="the turn to finish")
    await settle()

    # The turn's stop event was never set by the audio failure, and the request was
    # never replayed. The computed answer is the console's to display.
    assert stopped == [False]
    assert rig.submitted == ["what time is it"]
    assert [stage for stage, _ in rig.errors] == ["capture"]
    await rig.close()


@pytest.mark.anyio
async def test_a_failed_transcription_disables_input_without_submitting(tmp_path):
    stt = FakeSTT([], status="failed", error="whisper exited 2")
    rig = build(tmp_path, stt=stt)

    assert await rig.adapter.start() is True
    await say(rig)
    await wait_until(lambda: stt.calls, message="a transcription attempt")
    await wait_until(lambda: rig.errors, message="a transcription failure report")
    await settle()

    assert [stage for stage, _ in rig.errors] == ["stt"]
    assert rig.submitted == []
    assert rig.adapter.input_available is False
    assert rig.adapter.output_available is True
    await rig.close()


@pytest.mark.anyio
async def test_playback_failure_disables_speech_and_keeps_input(tmp_path):
    player = FakePlayer(fail="aplay: audio device is gone")
    rig = build(tmp_path, player=player)

    assert await rig.adapter.start() is True
    await say(rig)
    await wait_until(lambda: rig.submitted, message="the voice request")
    await wait_until(lambda: rig.errors, message="a playback failure report")
    await settle()

    # The ready cue may have failed first, and it is reported without latching; the
    # reply's playback is what disables speech.
    assert {stage for stage, _ in rig.errors} == {"playback"}
    assert rig.adapter.output_available is False
    assert rig.adapter.input_available is True
    assert rig.submitted == ["what time is it"]
    await rig.close()


# -- one turn, one submission -------------------------------------------------


@pytest.mark.anyio
async def test_a_voice_turn_submits_once_and_leaves_the_shared_memory_alone(tmp_path):
    rig = build(tmp_path)

    assert await rig.adapter.start() is True
    await say(rig)
    await wait_until(lambda: rig.tts.texts, message="the spoken answer")
    await settle()

    assert rig.submitted == ["what time is it"]
    # Display is the console's job; speech is the adapter's. Exactly one copy.
    assert rig.tts.texts == ["answer to what time is it"]
    # The shared history belongs to the controller, not to the audio path.
    assert rig.adapter.memory.turns == ()
    await rig.close()


@pytest.mark.anyio
async def test_a_typed_turn_discards_partial_capture_and_stale_transcription(tmp_path):
    gate = threading.Event()
    stt = FakeSTT(["stale request"], gate=gate)
    rig = build(tmp_path, stt=stt)

    assert await rig.adapter.start() is True
    await say(rig)
    await wait_until(lambda: stt.calls, message="the recording to reach the recogniser")

    # The typed request takes the foreground while the recording is still decoding.
    await rig.adapter.set_typed_turn(True)
    gate.set()
    await settle()

    assert rig.submitted == []
    assert stt.calls, "the stale transcription still has to finish and be dropped"
    await rig.close()


@pytest.mark.anyio
async def test_a_typed_turn_closes_the_followup_window_but_a_fresh_wake_interrupts(tmp_path):
    rig = build(tmp_path, interaction="conversation")

    assert await rig.adapter.start() is True
    await say(rig)
    await wait_until(lambda: rig.submitted, message="the first voice request")
    await wait_until(_in_state(rig, "FOLLOWUP"), message="follow-up window")

    await rig.adapter.set_typed_turn(True)
    # Speech without a wake phrase is not a request while a typed turn runs.
    rig.stream.feed(*SPEECH_FRAMES)
    await settle(0.2)
    assert rig.submitted == ["what time is it"]

    # A fresh wake is still the established interruption, typed turn or not.
    await say(rig)
    await wait_until(lambda: rig.stop_calls, message="the typed turn to be stopped")
    await wait_until(lambda: len(rig.submitted) == 2, message="the interrupting voice request")

    assert rig.submitted == ["what time is it", "what time is it"]
    assert rig.adapter.typed_turn is False
    await rig.close()


def _in_state(rig: Rig, state: str):
    def check() -> bool:
        session = rig.adapter._session
        return session is not None and session.state == state

    return check


# -- input toggling -----------------------------------------------------------


@pytest.mark.anyio
async def test_pausing_input_keeps_one_reader_and_drops_the_audio_it_saw(tmp_path):
    stream = FakeStream()
    rig = build(tmp_path, stream=stream, stream_factory=lambda: stream)

    assert await rig.adapter.start() is True
    await wait_until(lambda: stream.reads > 0, message="the first capture")
    reads_while_running = stream.reads

    await rig.adapter.set_input_enabled(False)
    assert rig.adapter.input_available is False
    # The device stays open: one reader for the whole session, not one per toggle.
    assert stream.starts == 1
    assert stream.closes == 0

    stream.feed(WAKE, *SPEECH_FRAMES)
    await settle()
    assert rig.submitted == []
    # Audio that arrived while paused is dropped, not a capture failure.
    assert rig.errors == []

    assert await rig.adapter.set_input_enabled(True) is True
    assert stream.starts == 1
    assert stream.reads >= reads_while_running
    await rig.close()

# -- interactions -------------------------------------------------------------


def _approval_entry(**overrides: Any) -> dict:
    entry = {
        "id": "req-1",
        "kind": "approval",
        "prompt": "Delete build/",
        "risk": "medium",
        "allowed_channels": ["terminal", "voice"],
        "operation": "filesystem.delete",
        "host": "local",
        "arguments": {"path": "build"},
        "scopes": ["workspace"],
        "expires_at": 4_000_000.0,
    }
    entry.update(overrides)
    return entry


@pytest.mark.anyio
async def test_a_pending_approval_is_resolved_through_the_injected_broker(tmp_path):
    entry = _approval_entry()
    broker = FakeBroker(entry)
    rig = build(tmp_path, broker=broker, stt=FakeSTT(["approve this action"]))

    assert await rig.adapter.start() is True
    handler = asyncio.create_task(rig.adapter.handle_interaction(entry))

    await wait_until(lambda: len(rig.player.played) >= 2, message="prompt and cue")
    await record(rig)
    await wait_until(lambda: broker.approvals, message="the spoken approval")

    assert broker.approvals == [("req-1", True, "voice")]
    # The request the broker sees is the same one it issued.
    assert set(broker.ready) <= {"req-1"}
    handler.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await handler
    await rig.close()


@pytest.mark.anyio
async def test_a_pending_approval_is_refused_without_touching_the_broker(tmp_path):
    entry = _approval_entry()
    broker = FakeBroker(entry)
    rig = build(tmp_path, broker=broker)

    assert await rig.adapter.start() is True
    await rig.adapter.set_input_enabled(False)

    with pytest.raises(AudioUnavailable):
        await rig.adapter.handle_interaction(entry)

    # The keyboard and the interaction API must still be able to answer it.
    assert broker.approvals == []
    assert broker.questions == []
    assert broker.ready == []
    assert broker.cancelled == []
    await rig.close()


@pytest.mark.anyio
async def test_speech_failure_during_a_prompt_does_not_approve_or_reset_it(tmp_path):
    entry = _approval_entry()
    broker = FakeBroker(entry)
    player = FakePlayer(fail="aplay: device busy")
    rig = build(tmp_path, broker=broker, player=player)

    assert await rig.adapter.start() is True
    handler = asyncio.create_task(rig.adapter.handle_interaction(entry))
    await wait_until(lambda: rig.errors, message="the playback failure")
    await settle()

    # Displayed, not approved, and still pending for the keyboard.
    assert broker.approvals == []
    assert entry["expires_at"] == 4_000_000.0
    assert rig.adapter.output_available is False
    handler.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await handler
    await rig.close()


# -- shutdown -----------------------------------------------------------------


@pytest.mark.anyio
async def test_close_stops_playback_and_drains_the_blocked_device_call(tmp_path):
    gate = threading.Event()
    player = FakePlayer(gate=gate)
    rig = build(tmp_path, player=player, drain_timeout=0.2)

    assert await rig.adapter.start() is True
    speaking = asyncio.create_task(rig.adapter.speak("a long answer"))
    await wait_until(lambda: player.played, message="playback to start")

    await asyncio.wait_for(rig.close(), timeout=3.0)
    assert player.stops >= 1
    assert rig.stream.closes >= 1
    assert rig.adapter.input_available is False

    gate.set()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(speaking, timeout=3.0)


@pytest.mark.anyio
async def test_closing_during_synthesis_never_starts_playback(tmp_path):
    gate = threading.Event()
    rig = build(tmp_path, tts=FakeTTS(gate=gate), drain_timeout=0.2)

    assert await rig.adapter.start() is True
    speaking = asyncio.create_task(rig.adapter.speak("an answer nobody will hear"))
    await wait_until(lambda: rig.tts.texts, message="synthesis to start")

    await asyncio.wait_for(rig.close(), timeout=3.0)
    gate.set()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(speaking, timeout=3.0)

    assert rig.player.played == []


def _config_with(tmp_path: Path, **overrides: Any) -> VoiceConfig:
    values: dict[str, Any] = dict(
        whisper_bin="whisper-cli",
        whisper_model=str(tmp_path / "ggml-tiny.en.bin"),
        kokoro_model=str(tmp_path / "kokoro.onnx"),
        kokoro_voices=str(tmp_path / "voices.bin"),
        tmp_dir=tmp_path / "voice",
        settling_ms=0,
        interaction="wake",
        wake_model=tmp_path / "hey_jarvis.onnx",
        stream_speech=False,
    )
    values.update(overrides)
    return VoiceConfig(**values)
