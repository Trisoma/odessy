"""Voice drives the selected runtime: deltas, cancellation and phase timing (prompt 13).

The interesting failures here cannot be seen from one module at a time:

* the engine emits text from its own worker thread, where the voice session's context
  variable is not visible, so a stream bound only by ``speech_scope`` would never be fed;
* a retried request or a tool call must not speak the previous attempt's unfinished text;
* a cancelled reply's buffer must not be spoken as if it were the answer;
* synthesis is not playback, so a missing voice must not record a first audible output.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from odessy.application.desktop_voice import DesktopVoice
from odessy.application.session import SessionController
from odessy.runtime.speech import SpeechStream, speech_scope
from odessy.runtime_backends.selection import RuntimeSelection
from odessy.voice.hybrid import HybridAudioAdapter
from odessy.voice.session import STOPPED_TEXT, SessionOptions, VoiceSession
from odessy.voice.timing import DELTA_SOURCE, SETTLED_SOURCE, VoiceTiming
from odessy.voice.config import VoiceConfig


# ----------------------------------------------------------------- engine fixture


class FakeEngine:
    """Stand-in for the pinned engine: emits its frames from worker threads.

    ``script`` is a list of ``(event_type, data)``. Text is emitted through
    ``session.emit_from_worker`` from a real executor thread, which is the case a
    context-variable-only binding fails on.
    """

    def __init__(self, script, *, answer: str = "", fail: BaseException | None = None):
        self.script = list(script)
        self.answer = answer
        self.fail = fail
        self.session = None
        self.identity = None
        self.acp_session_id = None
        self.emitted_at: list[float] = []
        self.received: list[str] = []

    async def run_turn(self, text: str) -> SimpleNamespace:
        session = self.session
        self.received.append(text)
        for event_type, data in self.script:
            await asyncio.to_thread(session.emit_from_worker, event_type, data)
            self.emitted_at.append(time.monotonic())
        if self.fail is not None:
            raise self.fail
        return SimpleNamespace(final_text=self.answer, metrics={})


def build_controller(monkeypatch, engine: FakeEngine) -> SessionController:
    def fake_resolve_and_build(session, value=None, *, environ=None):
        engine.session = session
        return RuntimeSelection(engine="harness", source="explicit"), engine

    monkeypatch.setattr(
        "odessy.application.session.resolve_and_build", fake_resolve_and_build
    )
    controller = SessionController(
        model_factory=lambda name: None,
        agent_factory=None,
        profiles={"local": {"name": "local"}},
        selected_profile="local",
    )
    assert controller.engine == "harness"
    return controller


def _delta(text: str, attempt: int = 1) -> tuple[str, dict]:
    return "delta", {"text": text, "attempt": attempt, "stream": "s", "seq": 1}


# ---------------------------------------------------------------- streaming path


@pytest.mark.anyio
async def test_engine_deltas_reach_the_speech_stream_from_a_worker_thread(monkeypatch):
    spoken: list[str] = []
    engine = FakeEngine(
        [_delta("It is noon. "), _delta("Enjoy lunch.")],
        answer="It is noon. Enjoy lunch.",
    )
    controller = build_controller(monkeypatch, engine)
    stream = SpeechStream(spoken.append)

    with speech_scope(stream):
        result = await controller.run_turn("what time is it")

    assert result.final_text == "It is noon. Enjoy lunch."
    assert spoken == ["It is noon.", "Enjoy lunch."]
    # The reply was complete, so the settled answer is the text already heard: the
    # voice session compares it and does not say it a second time.
    assert stream.last_text == "It is noon. Enjoy lunch."


@pytest.mark.anyio
async def test_a_text_turn_never_feeds_a_speech_stream(monkeypatch):
    engine = FakeEngine([_delta("unspoken")], answer="unspoken")
    controller = build_controller(monkeypatch, engine)
    stream = SpeechStream(lambda sentence: pytest.fail("nothing may be spoken"))

    await controller.run_turn("typed request")
    assert stream.emitted == 0


@pytest.mark.anyio
async def test_a_retry_drops_the_unfinished_buffer_of_the_earlier_attempt(monkeypatch):
    spoken: list[str] = []
    engine = FakeEngine(
        [
            _delta("Thinking", attempt=1),
            _delta("It is noon. ", attempt=2),
            _delta("Enjoy lunch.", attempt=2),
        ],
        answer="It is noon. Enjoy lunch.",
    )
    controller = build_controller(monkeypatch, engine)

    with speech_scope(SpeechStream(spoken.append)):
        await controller.run_turn("what time is it")

    # "Thinking" had no sentence end, so it stayed buffered; a retry must not speak it.
    assert spoken == ["It is noon.", "Enjoy lunch."]


@pytest.mark.anyio
async def test_a_tool_call_stops_the_rest_of_that_reply_from_being_spoken(monkeypatch):
    spoken: list[str] = []
    engine = FakeEngine(
        [
            _delta("Let me check. "),
            ("tool_progress", {"name": "disk_usage", "state": "running", "call_id": "c1"}),
            _delta("The disk is fine. "),
        ],
        answer="The disk is fine.",
    )
    controller = build_controller(monkeypatch, engine)
    stream = SpeechStream(spoken.append)

    with speech_scope(stream):
        await controller.run_turn("check the disk")

    assert spoken == ["Let me check."]
    # The answer arrived while the tool call held the reply: it is not the spoken text,
    # so the voice session still says the settled answer instead of staying silent.
    assert stream.last_text == ""


@pytest.mark.anyio
async def test_a_cancelled_reply_does_not_speak_its_partial_text(monkeypatch):
    spoken: list[str] = []
    engine = FakeEngine(
        [_delta("Never fully spoken")],  # no sentence end: still buffered
        fail=asyncio.CancelledError(),
    )
    controller = build_controller(monkeypatch, engine)
    stream = SpeechStream(spoken.append)

    with speech_scope(stream), pytest.raises(asyncio.CancelledError):
        await controller.run_turn("long task")

    assert spoken == []
    assert stream.emitted == 0
    assert "stopped" in [event["type"] for event in controller.events]


@pytest.mark.anyio
async def test_the_voice_session_speaks_engine_sentences_once(monkeypatch):
    spoken: list[str] = []
    engine = FakeEngine(
        [_delta("It is noon. "), _delta("Enjoy lunch.")],
        answer="It is noon. Enjoy lunch.",
    )
    controller = build_controller(monkeypatch, engine)
    session = voice_session(controller, spoken)

    await session._run("what time is it")

    # The transcript took the same path a typed turn does: one admission into the
    # session's selected engine, which received the recognised text unchanged.
    assert engine.received == ["what time is it"]
    assert spoken == ["It is noon.", "Enjoy lunch."]
    assert STOPPED_TEXT not in spoken


@pytest.mark.anyio
async def test_a_stopped_turn_says_stopped_and_drops_pending_sentences(monkeypatch):
    spoken: list[str] = []
    engine = FakeEngine(
        [_delta("This is never heard. ")], answer="This is never heard."
    )
    controller = build_controller(monkeypatch, engine)
    session = voice_session(controller, spoken)

    async def run_turn(text, stop, context):
        # The user stopped while the engine was still producing text.
        stop.set()
        return await controller.run_turn(text)

    session.run_turn = run_turn
    await session._run("long task")

    assert spoken == [STOPPED_TEXT]


# --------------------------------------------------------------------- timing path


@pytest.mark.anyio
async def test_the_first_engine_text_is_timed_with_its_source(monkeypatch):
    timing = VoiceTiming().begin("turn-1")
    engine = FakeEngine([_delta("It is noon.")], answer="It is noon.")
    controller = build_controller(monkeypatch, engine)
    controller.bind_turn_timing(timing)

    with speech_scope(SpeechStream(lambda sentence: None)):
        await controller.run_turn("time?")

    snapshot = timing.public()
    assert snapshot["first_text_source"] == DELTA_SOURCE
    assert snapshot["first_text_ms"] is not None


@pytest.mark.anyio
async def test_a_settled_answer_is_not_labelled_as_a_delta(monkeypatch):
    timing = VoiceTiming().begin("turn-1")
    engine = FakeEngine([], answer="no deltas here")
    controller = build_controller(monkeypatch, engine)
    controller.bind_turn_timing(timing)

    await controller.run_turn("time?")
    # No delta reached the session, so nothing was timed as runtime text.
    assert timing.has_first_text is False

    timing.mark_first_text(SETTLED_SOURCE)
    assert timing.public()["first_text_source"] == SETTLED_SOURCE


def _config(tmp_path) -> VoiceConfig:
    return VoiceConfig(
        whisper_bin="whisper-cli",
        whisper_model="model.bin",
        kokoro_model="kokoro.onnx",
        kokoro_voices="voices.bin",
        tmp_dir=tmp_path,
        audio_backend="command",
    )


class _STT:
    def __init__(self, text: str = "what time is it", status: str = "ok") -> None:
        self.text, self.status = text, status

    def transcribe(self, path):
        return SimpleNamespace(status=self.status, text=self.text, error="")


class _TTS:
    def __init__(self, status: str = "ok") -> None:
        self.status = status

    def synthesize(self, text, output_path):
        from pathlib import Path

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"RIFF")
        return SimpleNamespace(status=self.status, error="synthesis failed",
                              output_path=Path(output_path))


class _Player:
    generation = 0

    def __init__(self) -> None:
        self.played: list[str] = []

    def play(self, path, *, generation=None) -> None:
        self.played.append(str(path))

    def stop(self) -> None:
        pass


@pytest.mark.anyio
async def test_capture_recognition_and_first_audio_are_measured_separately(tmp_path):
    adapter = HybridAudioAdapter(
        config=_config(tmp_path),
        submit_voice=lambda text, stop, context: None,
        stt_factory=_STT,
        tts_factory=_TTS,
        player_factory=_Player,
    )
    snapshots: list[dict] = []
    adapter.report_timing = snapshots.append
    await adapter._prepare_output()
    adapter._started = True

    adapter.begin_capture("push-to-talk")
    adapter.note_capture_end()
    adapter._stt = _STT()  # the push-to-talk seam's recogniser, without a device
    assert await adapter.transcribe_pcm(b"\x00\x00" * 400) == "what time is it"

    before = adapter.timing.public()
    assert before["capture_ms"] is not None
    assert before["stt_ms"] is not None
    assert before["first_text_ms"] is None and before["first_audio_ms"] is None
    assert "first_audio" in before["not_measured"]

    # The application marks the first engine text on the same object.
    adapter.timing.mark_first_text(DELTA_SOURCE)
    await adapter.speak("It is noon.")

    after = adapter.timing.public()
    assert after["first_audio_ms"] is not None
    assert "first_audio" not in after["not_measured"]
    # Distinct phases: recognition is not folded into capture, and audio is not text.
    assert adapter.timing.capture_end <= adapter.timing.stt_done
    assert snapshots and {"capture_ms", "stt_ms"} <= set(snapshots[0])
    await adapter.aclose()


@pytest.mark.anyio
async def test_missing_synthesis_does_not_claim_a_first_audible_output(tmp_path):
    adapter = HybridAudioAdapter(
        config=_config(tmp_path),
        submit_voice=lambda text, stop, context: None,
        stt_factory=_STT,
        tts_factory=lambda: _TTS(status="failed"),
        player_factory=_Player,
    )
    failures: list[tuple[str, str]] = []
    adapter.report_audio_error = lambda stage, message: failures.append((stage, message))
    await adapter._prepare_output()
    adapter._started = True
    adapter.begin_capture("turn")
    adapter.note_capture_end()
    adapter.timing.mark_first_text(DELTA_SOURCE)

    await adapter.speak("It is noon.")

    assert adapter.timing.first_audio is None
    assert adapter.timing.public()["first_audio_ms"] is None
    assert failures and failures[0][0] == "synthesis"
    await adapter.aclose()


class _FakeController:
    """The parts of a session ``DesktopVoice`` is allowed to touch."""

    def __init__(self) -> None:
        self.id = "s1"
        self.running = False
        self.interactions = SimpleNamespace(pending=[], handler=None,
                                            is_pending=lambda identifier: False,
                                            mark_ready=lambda identifier: None)
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type, data=None):
        self.events.append((event_type, data or {}))

    def _public(self, value):
        return value


class _SilentAudio:
    """An audio adapter whose playback fails: nothing was audible."""

    def __init__(self) -> None:
        self.timing = VoiceTiming().begin("turn")
        self.reports: list[dict] = []

    def report_timing_to(self, callback) -> None:
        self.reports.append({"bound": True})

    async def speak(self, text: str) -> None:
        raise RuntimeError("no speech output on this computer")


@pytest.mark.anyio
async def test_a_failed_playback_reports_an_audio_error_not_a_spoken_answer():
    controller = _FakeController()
    voice = DesktopVoice(lambda: _SilentAudio(), host="test-host")

    await voice.speak(controller, "It is noon.")

    types = [event_type for event_type, _ in controller.events]
    assert "audio_error" in types
    # The answer was displayed already; nothing claims it was heard.
    assert voice.state == "idle"
    assert voice.snapshot(controller)["state"] == "idle"


@pytest.mark.anyio
async def test_a_fresh_wake_phrase_stops_the_running_turn_through_the_normal_stop():
    from odessy.application.api import _stop_when_asked

    class _Controller:
        def __init__(self) -> None:
            self.stops = 0

        async def stop(self):
            self.stops += 1
            return {"status": "stopped"}

    controller = _Controller()
    stop = asyncio.Event()
    waiter = asyncio.create_task(_stop_when_asked(controller, stop))
    await asyncio.sleep(0)
    assert controller.stops == 0  # not stopped while the user is still speaking

    stop.set()
    await waiter
    assert controller.stops == 1


@pytest.mark.anyio
async def test_a_cancelled_barge_in_waiter_does_not_stop_the_turn():
    from odessy.application.api import _stop_when_asked

    class _Controller:
        def __init__(self) -> None:
            self.stops = 0

        async def stop(self):
            self.stops += 1

    controller = _Controller()
    waiter = asyncio.create_task(_stop_when_asked(controller, asyncio.Event()))
    await asyncio.sleep(0)
    waiter.cancel()  # the turn ended on its own first
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert controller.stops == 0


class _BrokenPlayer(_Player):
    """A player whose device fails on the first sample."""

    def play(self, path, *, generation=None) -> None:
        raise RuntimeError("no output device")


@pytest.mark.anyio
async def test_playback_failure_withdraws_the_first_audio_mark(tmp_path):
    """A mark taken before playback must not survive a player that never started."""

    adapter = HybridAudioAdapter(
        config=_config(tmp_path),
        submit_voice=lambda text, stop, context: None,
        stt_factory=_STT,
        tts_factory=_TTS,
        player_factory=_BrokenPlayer,
    )
    failures: list[tuple[str, str]] = []
    adapter.report_audio_error = lambda stage, message: failures.append((stage, message))
    await adapter._prepare_output()
    adapter._started = True
    adapter.begin_capture("turn")
    adapter.note_capture_end()
    adapter.timing.mark_first_text(DELTA_SOURCE)

    await adapter.speak("It is noon.")

    assert adapter.timing.first_audio is None
    assert adapter.timing.public()["first_audio_ms"] is None
    assert "first_audio" in adapter.timing.public()["not_measured"]
    assert failures and failures[0][0] == "playback"
    await adapter.aclose()


def test_a_cancelled_turn_has_no_first_text_to_claim():
    """No settled answer means no ``settled-answer`` phase: it stays unmeasured."""

    snapshot = VoiceTiming().begin("turn-1").settle(settled=False)

    assert snapshot["first_text_ms"] is None
    assert snapshot["first_text_source"] is None
    assert "first_text" in snapshot["not_measured"]


def test_a_settled_turn_names_the_answer_as_its_first_text():
    snapshot = VoiceTiming().begin("turn-1").settle(settled=True)

    assert snapshot["first_text_source"] == SETTLED_SOURCE
    assert snapshot["first_text_ms"] is not None


@pytest.mark.anyio
async def test_push_to_talk_marks_answer_before_playback(monkeypatch):
    """Playback duration must not become model time or a negative audio interval."""
    import httpx
    from odessy.application.api import create_app

    clock = SimpleNamespace(now=10.0)
    monkeypatch.setattr("odessy.voice.timing.time", SimpleNamespace(monotonic=lambda: clock.now))
    class Engine(FakeEngine):
        def events(self):
            return []

        async def close(self):
            pass

    controller = build_controller(monkeypatch, Engine([], answer="The answer."))

    class Audio:
        def __init__(self):
            self.timing = VoiceTiming()
            self.at_playback = None

        async def start(self):
            self.timing.begin("push-to-talk")

        async def finish(self):
            clock.now = 11.0
            self.timing.mark_capture_end()
            clock.now = 12.0
            self.timing.mark_stt_done()
            return "question"

        async def speak(self, text):
            self.at_playback = self.timing.public()
            clock.now = 13.0
            self.timing.mark_first_audio()
            clock.now = 20.0  # Seven seconds of playback are not engine latency.

        async def cancel(self):
            pass

        async def close(self):
            pass

    audio = Audio()
    voice = DesktopVoice(lambda: audio, host="test")
    app = create_app(lambda **kwargs: controller, desktop_voice=voice, token="voice-test")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Authorization": "Bearer voice-test"}) as client:
            sid = (await client.post("/v1/sessions")).json()["id"]
            path = f"/v1/sessions/{sid}/voice"
            capture = (await client.post(path, json={"action": "start_capture"})).json()
            response = await client.post(path, json={"action": "finish_capture", "capture_id": capture["capture_id"]})
            assert response.status_code == 200
            for _ in range(100):
                if audio.at_playback is not None:
                    break
                await asyncio.sleep(0.001)
            assert audio.at_playback is not None
            assert audio.at_playback["first_text_source"] == SETTLED_SOURCE
            assert audio.at_playback["first_text_ms"] == 0.0
            assert audio.timing.public()["first_audio_ms"] == 1000.0
            assert audio.timing.public()["first_audio_measurement"] == "playback-request"
    finally:
        await voice.close()
        await controller.close()


@pytest.mark.anyio
async def test_text_arriving_after_the_turn_ended_is_not_spoken(monkeypatch):
    """A frame from the engine's worker thread after the turn closed has no turn."""

    spoken: list[str] = []
    engine = FakeEngine([], answer="done")
    controller = build_controller(monkeypatch, engine)
    stream = SpeechStream(spoken.append)

    with speech_scope(stream):
        await controller.run_turn("first")
    controller.emit_from_worker("delta", {"text": "stale sentence.", "attempt": 1,
                                           "stream": "s", "seq": 9})
    await asyncio.sleep(0.05)

    assert spoken == []
    assert stream.emitted == 0


@pytest.mark.anyio
async def test_the_settled_answer_is_not_repeated_after_a_tool_call(monkeypatch):
    """The answer joins every message of the turn; the spoken words keep their place."""

    spoken: list[str] = []
    engine = FakeEngine(
        [_delta("Let me check. ", attempt=1),
         ("tool_progress", {"name": "disk_usage", "state": "running", "call_id": "c1"}),
         _delta("The disk is fine.", attempt=2)],
        answer="Let me check. The disk is fine.",
    )
    controller = build_controller(monkeypatch, engine)
    session = voice_session(controller, spoken)

    await session._run("check the disk")

    assert spoken == ["Let me check.", "The disk is fine."]


@pytest.mark.anyio
async def test_only_the_unheard_part_of_the_answer_is_spoken(monkeypatch):
    """A tool call holds the rest of its reply, so the answer continues from there."""

    spoken: list[str] = []
    engine = FakeEngine(
        [_delta("Let me check. ", attempt=1),
         ("tool_progress", {"name": "disk_usage", "state": "running", "call_id": "c1"}),
         _delta("The disk is fine.", attempt=1)],
        answer="Let me check. The disk is fine.",
    )
    controller = build_controller(monkeypatch, engine)
    session = voice_session(controller, spoken)

    await session._run("check the disk")

    assert spoken == ["Let me check.", "The disk is fine."]


@pytest.mark.anyio
async def test_a_superseded_turn_speaks_nothing():
    """A newer activation owns the audio: the old turn's queued sentences are dropped."""

    spoken: list[str] = []
    session = voice_session(SimpleNamespace(), spoken)

    stream, _speaker, finish = session._start_streaming(session._turn_id)
    stream.feed("Superseded sentence. ")
    session._turn_id += 1  # a newer activation started while this reply was streaming
    await finish()

    assert spoken == []
    assert stream.emitted == 1  # it was produced; it was simply not spoken


# ------------------------------------------------------------------------ helpers


def voice_session(controller: SessionController, spoken: list[str]) -> VoiceSession:
    async def frames() -> bytes:
        return b"\x00" * 1280

    async def speak(text: str) -> None:
        spoken.append(text)

    async def run_turn(text: str, stop: asyncio.Event, context) -> str:
        return (await controller.run_turn(text)).final_text

    class _Detectors:
        frame_bytes = 1280

        def score(self, pcm: bytes) -> float:
            return 0.0

        def is_speech(self, pcm: bytes) -> bool:
            return False

        def reset(self) -> None:
            pass

    return VoiceSession(
        frames=frames,
        wake=_Detectors(),
        gate=SimpleNamespace(accepts=lambda score, frames_since_activation: False),
        vad=_Detectors(),
        transcribe=lambda pcm: None,
        speak=speak,
        stop_speaking=lambda: None,
        run_turn=run_turn,
        classify_blank=lambda text: None,
        options=SessionOptions(),
        stream_speech=True,
    )
