"""Wake-word session behaviour, driven by recorded PCM.

Everything here runs on synthetic frames with a content-driven fake VAD and a scripted
transcriber, so the tests exercise the real session logic — the same reader, worker,
segmenter, and interruption paths the device uses — without a microphone, a speaker, a
model, or a sleeping clock.

Frame *contents* are the script: a frame starting with ``0x01`` is speech, ``0x02`` is
the wake phrase, ``0x00`` is silence. The fakes read those bytes, so no test has to
count frames or know about reframing.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import replace
from typing import Any

import pytest

from odessy.voice.frames import (
    VAD_FRAME_BYTES,
    WAKE_FRAME_BYTES,
    AudioFrame,
    FrameBuffer,
    FrameQueue,
)
from odessy.voice.memory import ConversationMemory
from odessy.voice.segmentation import SegmenterConfig, UtteranceSegmenter
from odessy.voice.session import (
    CANCEL_PHRASES,
    STOPPED_TEXT,
    SessionOptions,
    VoiceSession,
    pcm_to_wav,
)
from odessy.voice.stt import blank_transcript_reason
from odessy.voice.wake import WakeGate

SILENCE = b"\x00" * WAKE_FRAME_BYTES
SPEECH = b"\x01" * WAKE_FRAME_BYTES
WAKE = b"\x02" * WAKE_FRAME_BYTES


class ScriptedFrames:
    """Plays a script once, then silence forever — like a microphone that stays open."""

    def __init__(self, frames: list[bytes], *, fail_at_end: bool = False) -> None:
        self._frames = list(frames)
        self._index = 0
        self._fail_at_end = fail_at_end

    @property
    def exhausted(self) -> bool:
        return self._index >= len(self._frames)

    async def __call__(self) -> bytes:
        if self._index >= len(self._frames):
            if self._fail_at_end:
                raise RuntimeError("device disappeared")
            return SILENCE
        frame = self._frames[self._index]
        self._index += 1
        return frame


class FakeWake:
    def __init__(self) -> None:
        self.frame_bytes = WAKE_FRAME_BYTES
        self.resets = 0

    def score(self, pcm: bytes) -> float:
        return 0.9 if pcm[0:1] == b"\x02" else 0.0

    def reset(self) -> None:
        self.resets += 1


class FakeVad:
    def __init__(self) -> None:
        self.frame_bytes = VAD_FRAME_BYTES
        self.resets = 0

    def is_speech(self, pcm: bytes) -> bool:
        return pcm[0:1] == b"\x01"

    def reset(self) -> None:
        self.resets += 1


class Harness:
    def __init__(
        self,
        frames: list[bytes],
        *,
        transcripts: list[str] | None = None,
        fail_at_end: bool = False,
    ) -> None:
        self.source = ScriptedFrames(frames, fail_at_end=fail_at_end)
        self.transcripts = list(transcripts if transcripts is not None else ["what time is it"])
        self.transcribed: list[bytes] = []
        self.spoken: list[str] = []
        self.turns: list[str] = []
        self.contexts: list[Any] = []
        self.statuses: list[str] = []
        self.cues = 0
        self.stops = 0
        self.turn_started = asyncio.Event()
        self.turn_gate: asyncio.Event | None = None
        self.session: VoiceSession | None = None

    async def transcribe(self, pcm: bytes) -> str:
        self.transcribed.append(pcm)
        return self.transcripts.pop(0) if self.transcripts else ""

    async def speak(self, text: str) -> None:
        self.spoken.append(text)

    def stop_speaking(self) -> None:
        self.stops += 1

    async def run_turn(self, text: str, stop: asyncio.Event, context: Any) -> str:
        self.turns.append(text)
        self.contexts.append(context)
        self.turn_started.set()
        if self.turn_gate is not None:
            await self.turn_gate.wait()
        if stop.is_set():
            return STOPPED_TEXT
        return f"answer to {text}"

    async def cue(self) -> None:
        self.cues += 1

    def build(self, *, interaction: str = "wake", blank_retries: int = 2) -> VoiceSession:
        self.session = VoiceSession(
            frames=self.source,
            wake=FakeWake(),
            gate=WakeGate(threshold=0.5),
            vad=FakeVad(),
            transcribe=self.transcribe,
            speak=self.speak,
            stop_speaking=self.stop_speaking,
            run_turn=self.run_turn,
            classify_blank=blank_transcript_reason,
            segmenter=UtteranceSegmenter(
                SegmenterConfig(
                    min_speech_ms=64,
                    silence_end_ms=64,
                    speech_start_timeout_ms=160,
                    max_utterance_ms=4000,
                )
            ),
            options=SessionOptions(interaction=interaction, blank_retries=blank_retries),
            memory=ConversationMemory(),
            status=self.statuses.append,
            cue=self.cue,
        )
        return self.session


async def drive(
    harness: Harness,
    *,
    interaction: str = "wake",
    blank_retries: int = 2,
    timeout: float = 5.0,
) -> None:
    """Run the session until the script is consumed, then stop it.

    Waiting on the script rather than on a wall-clock sleep keeps the tests
    deterministic; the short settle afterwards lets the worker finish a turn whose
    frames were consumed microseconds earlier.
    """

    session = harness.session or harness.build(
        interaction=interaction, blank_retries=blank_retries
    )
    assert session is not None
    task = asyncio.create_task(session.run())
    deadline = time.monotonic() + timeout
    try:
        while not harness.source.exhausted:
            if time.monotonic() > deadline:
                raise AssertionError("the scripted audio was never consumed")
            await asyncio.sleep(0.002)
        settle = time.monotonic() + 0.2
        while time.monotonic() < settle:
            await asyncio.sleep(0.005)
    finally:
        await session.aclose()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


# -- framing ------------------------------------------------------------------


def test_frames_are_recut_without_losing_bytes() -> None:
    buffer = FrameBuffer(VAD_FRAME_BYTES)

    produced = buffer.push(WAKE)

    assert len(produced) == 2, "80 ms holds two whole 32 ms frames"
    assert all(len(frame) == VAD_FRAME_BYTES for frame in produced)
    assert buffer.push(b"") == [], "the remainder is carried, not emitted early"


def test_a_queue_reports_overflow_instead_of_dropping_silently() -> None:
    queue = FrameQueue(max_frames=2)
    for index in range(3):
        queue.push(AudioFrame(pcm=SILENCE, index=index))

    assert queue.overflowed is True
    assert len(queue) == 2


# -- wake gate ----------------------------------------------------------------


def test_a_score_below_threshold_never_activates() -> None:
    assert WakeGate(threshold=0.5).accepts(0.49, frames_since_activation=None) is False


def test_the_first_activation_is_accepted_and_the_next_is_debounced() -> None:
    gate = WakeGate(threshold=0.5, debounce_ms=800, frame_ms=80)

    assert gate.accepts(0.6, frames_since_activation=None) is True
    assert gate.accepts(0.9, frames_since_activation=3) is False
    assert gate.accepts(0.9, frames_since_activation=10) is True


# -- segmentation -------------------------------------------------------------


def test_silence_that_never_becomes_speech_is_not_a_request() -> None:
    segmenter = UtteranceSegmenter(
        SegmenterConfig(frame_ms=32, speech_start_timeout_ms=160, min_speech_ms=64)
    )
    segmenter.begin()

    states = [segmenter.push(b"\x00" * VAD_FRAME_BYTES, False) for _ in range(6)]

    assert states[-1] == "no_speech"


def test_an_utterance_ends_after_the_silence_run() -> None:
    segmenter = UtteranceSegmenter(
        SegmenterConfig(frame_ms=32, min_speech_ms=64, silence_end_ms=64)
    )
    segmenter.begin()
    for _ in range(4):
        assert segmenter.push(b"\x01" * VAD_FRAME_BYTES, True) == "recording"

    states = [segmenter.push(b"\x00" * VAD_FRAME_BYTES, False) for _ in range(3)]

    assert states[-1] == "ended"
    assert segmenter.speech_ms >= 64


def test_a_short_noise_burst_is_not_an_utterance() -> None:
    segmenter = UtteranceSegmenter(
        SegmenterConfig(frame_ms=32, min_speech_ms=300, silence_end_ms=64)
    )
    segmenter.begin()
    segmenter.push(b"\x01" * VAD_FRAME_BYTES, True)

    states = [segmenter.push(b"\x00" * VAD_FRAME_BYTES, False) for _ in range(3)]

    assert states[-1] == "no_speech"


def test_the_ceiling_ends_a_neverending_utterance() -> None:
    segmenter = UtteranceSegmenter(
        SegmenterConfig(frame_ms=32, min_speech_ms=32, silence_end_ms=100_000, max_utterance_ms=160)
    )
    segmenter.begin()

    states = [segmenter.push(b"\x01" * VAD_FRAME_BYTES, True) for _ in range(6)]

    assert states[-1] == "ended"


# -- memory -------------------------------------------------------------------


def test_memory_drops_oldest_pairs_first() -> None:
    memory = ConversationMemory(max_exchanges=2)
    for index in range(3):
        memory.add(f"user {index}", f"answer {index}")

    assert [turn.user for turn in memory.turns] == ["user 1", "user 2"]


def test_memory_redacts_what_the_shared_filter_catches() -> None:
    memory = ConversationMemory()

    memory.add("ANTHROPIC_API_KEY=sk-ant-1 and the weather", "noted")

    assert "sk-ant-1" not in memory.turns[0].user


def test_memory_keeps_a_short_secret_below_the_filter_floor() -> None:
    """A recorded limitation, not a bug: redaction has minimum lengths.

    Two independent floors decide this, and neither is about the name list:
    ``NAME=value`` only fires for values of at least 6 characters, and the shape
    patterns need at least 16 characters after the prefix. The value below is 15, so
    it survives. That is why the memory is RAM-only and bounded rather than treated as
    safe.
    """

    memory = ConversationMemory()

    memory.add("my key is sk-live-1234567890", "noted")
    memory.add("DATABASE_PASSWORD=zzz", "noted")

    assert "sk-live-1234567890" in memory.turns[0].user
    assert "zzz" in memory.turns[1].user


def test_memory_honours_the_character_budget() -> None:
    memory = ConversationMemory(max_exchanges=6, max_chars=40)

    memory.add("a" * 30, "b" * 30)

    assert sum(len(turn.user) + len(turn.assistant) for turn in memory.turns) <= 40


def test_memory_is_empty_until_an_exchange_completes() -> None:
    memory = ConversationMemory()

    assert memory.context().turns == ()
    memory.add("hello", "hi")
    assert memory.context().turns == memory.turns
    memory.clear()
    assert memory.turns == ()


# -- the session --------------------------------------------------------------


@pytest.mark.anyio
async def test_nothing_is_transcribed_or_run_before_activation() -> None:
    harness = Harness([SILENCE] * 20)

    await drive(harness)

    assert harness.transcribed == []
    assert harness.turns == []
    assert harness.cues == 0


@pytest.mark.anyio
async def test_wake_then_speech_runs_one_turn_and_speaks_the_answer() -> None:
    harness = Harness([WAKE, *([SPEECH] * 4), *([SILENCE] * 6)])

    await drive(harness)

    assert harness.cues == 1
    assert harness.transcribed, "the utterance should reach the transcriber"
    assert harness.turns == ["what time is it"]
    assert harness.spoken == ["answer to what time is it"]


@pytest.mark.anyio
async def test_a_blank_transcript_is_retried_and_never_reaches_the_model() -> None:
    harness = Harness(
        [WAKE, *([SPEECH] * 4), SILENCE, *([SPEECH] * 4), *([SILENCE] * 6)],
        transcripts=["[BLANK_AUDIO]", "open the file"],
    )

    await drive(harness)

    assert harness.turns == ["open the file"]
    assert len(harness.transcribed) == 2


@pytest.mark.anyio
async def test_blank_retries_are_bounded_and_never_reuse_a_request() -> None:
    harness = Harness(
        [WAKE, *([SPEECH] * 4), SILENCE, *([SPEECH] * 4), *([SILENCE] * 6)],
        transcripts=["[BLANK_AUDIO]", "[BLANK_AUDIO]"],
    )

    await drive(harness, blank_retries=1)

    assert harness.turns == []
    assert any("nothing was sent to the model" in status for status in harness.statuses)


@pytest.mark.anyio
@pytest.mark.parametrize("phrase", sorted(CANCEL_PHRASES))
async def test_a_cancel_phrase_is_handled_without_the_model(phrase: str) -> None:
    harness = Harness([WAKE, *([SPEECH] * 4), *([SILENCE] * 6)], transcripts=[phrase])

    await drive(harness)

    assert harness.turns == []
    assert STOPPED_TEXT in harness.statuses
    assert harness.spoken == []


@pytest.mark.anyio
async def test_conversation_mode_takes_a_follow_up_without_the_wake_phrase() -> None:
    harness = Harness(
        [
            WAKE, *([SPEECH] * 4), *([SILENCE] * 6),
            *([SPEECH] * 4), *([SILENCE] * 6),
        ],
        transcripts=["first question", "and the second"],
    )

    await drive(harness, interaction="conversation")

    assert harness.turns == ["first question", "and the second"]


@pytest.mark.anyio
async def test_wake_mode_does_not_take_a_follow_up() -> None:
    harness = Harness(
        [
            WAKE, *([SPEECH] * 4), *([SILENCE] * 6),
            *([SPEECH] * 4), *([SILENCE] * 6),
        ],
        transcripts=["first question", "should not run"],
    )

    await drive(harness)

    assert harness.turns == ["first question"]


@pytest.mark.anyio
async def test_history_is_passed_to_later_turns_only() -> None:
    harness = Harness(
        [
            WAKE, *([SPEECH] * 4), *([SILENCE] * 6),
            *([SPEECH] * 4), *([SILENCE] * 6),
        ],
        transcripts=["first question", "second question"],
    )

    await drive(harness, interaction="conversation")

    first, second = harness.contexts
    assert first.turns == ()
    assert [turn.user for turn in second.turns] == ["first question"]


@pytest.mark.anyio
async def test_a_wake_during_a_turn_stops_it_before_the_answer_is_spoken() -> None:
    harness = Harness(
        [WAKE, *([SPEECH] * 4), *([SILENCE] * 6), *([SILENCE] * 14), WAKE, *([SILENCE] * 4)],
        transcripts=["first question"],
    )
    # Hold the first turn open so the second wake arrives while it is running.
    harness.turn_gate = asyncio.Event()
    session = harness.build()

    task = asyncio.create_task(session.run())
    await asyncio.wait_for(harness.turn_started.wait(), timeout=5)
    await asyncio.sleep(0.05)
    # The stop belongs to the turn that was interrupted; a replacement turn gets a
    # fresh event, so asserting on `_stop` now would test the wrong turn.
    assert harness.stops >= 1, "playback should have been cut"
    harness.turn_gate.set()
    await asyncio.sleep(0.2)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    await session.aclose()

    assert "answer to first question" not in harness.spoken, (
        "the interrupted turn's answer must never be spoken"
    )


@pytest.mark.anyio
async def test_a_device_failure_ends_admission_and_reports_the_error() -> None:
    harness = Harness([SILENCE] * 3, fail_at_end=True)
    session = harness.build()

    task = asyncio.create_task(session.run())
    deadline = time.monotonic() + 5
    while session.state != "ERROR" and time.monotonic() < deadline:
        await asyncio.sleep(0.005)
    await asyncio.wait_for(task, timeout=5)

    assert session.state == "ERROR"
    assert "microphone capture" in (session.error or "")


# -- wav container ------------------------------------------------------------


def test_pcm_is_wrapped_as_a_playable_wav() -> None:
    data = pcm_to_wav(b"\x00\x00" * 160)

    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"
    assert len(data) > 320

@pytest.mark.anyio
@pytest.mark.parametrize('action', ['activate', 'cancel', 'fail'])
async def test_superseding_actions_discard_pending_requests(action):
    session = Harness([]).build()
    session._pending = 'obsolete replacement'
    session._pending_ready.set()
    stop = session._stop
    if action == 'activate':
        await session._activate()
    elif action == 'cancel':
        await session._submit('cancel', session._turn_id)
    else:
        session._fail('device lost')
    assert session._pending is None
    assert stop.is_set()

@pytest.mark.anyio
async def test_transcription_does_not_block_wake_and_stale_text_is_discarded():
    session = Harness([]).build()
    started, release = asyncio.Event(), asyncio.Event()
    async def transcribe(audio):
        started.set()
        await release.wait()
        return 'obsolete request'
    session.transcribe = transcribe
    capture = asyncio.create_task(session._finish_capture())
    await started.wait()
    try:
        assert capture.done(), 'capture must hand STT to a background task'
        await session._activate()
    finally:
        release.set()
        await capture
        await asyncio.sleep(0)
    assert session._pending is None

@pytest.mark.anyio
async def test_generation_lock_survives_repeated_cancellation():
    import threading
    from odessy.runtime.agent_loop import OdessyAgent
    agent = object.__new__(OdessyAgent)
    agent._model_lock = asyncio.Lock()
    started, release = threading.Event(), threading.Event()
    def generate():
        started.set()
        release.wait(3)
    first = asyncio.create_task(agent._generate(generate))
    await asyncio.to_thread(started.wait, 2)
    try:
        first.cancel()
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.sleep(0)
        assert agent._model_lock.locked()
        assert not first.done()
    finally:
        release.set()
        with contextlib.suppress(asyncio.CancelledError):
            await first

@pytest.mark.anyio
async def test_live_capture_overflow_stops_admission():
    harness = Harness([WAKE])
    session = harness.build()
    overflow = asyncio.Event()
    async def cue():
        await overflow.wait()
    session.cue = cue
    def status(message):
        if 'overflow' in message:
            overflow.set()
    session.status = status
    await asyncio.wait_for(session.run(), 2)
    assert 'overflow' in session.error
    assert session.state == 'ERROR'
    assert session._stop.is_set()
    assert harness.turns == []

@pytest.mark.anyio
async def test_cli_drops_reply_interrupted_during_synthesis(tmp_path):
    import ast
    import uuid
    from pathlib import Path
    from types import SimpleNamespace
    import odessy.cli as cli
    session = SimpleNamespace(_turn_id=1, _closing=False)
    played = []
    def synthesize(text, path):
        path.write_bytes(b'audio')
        session._turn_id += 1
        return SimpleNamespace(status='ok')
    tree = ast.parse(Path(cli.__file__).read_text(encoding='utf-8'))
    speak = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'speak')
    namespace = dict(asyncio=asyncio, contextlib=contextlib, uuid=uuid,
                     config=SimpleNamespace(tmp_dir=tmp_path), session=session,
                     tts=SimpleNamespace(synthesize=synthesize),
                     player=SimpleNamespace(play=played.append))
    exec(compile(ast.Module(body=[speak], type_ignores=[]), str(cli.__file__), 'exec'), namespace)
    await namespace['speak']('old reply')
    assert played == []
    assert list(tmp_path.iterdir()) == []
