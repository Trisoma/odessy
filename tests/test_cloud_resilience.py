"""Cloud failover, streamed speech, and the egress log."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import odessy.models.cloud_model as cloud
from odessy.authority.egress import egress_file, format_record, read_records
from odessy.models.cloud_model import (
    CloudModel,
    CloudModelConfigError,
    CloudUnavailableError,
    _TransientCloudError,
    read_chat_stream,
)
from odessy.models.failover import (
    FailoverConfig,
    FailoverModel,
    build_cloud_model_with_failover,
    fallback_env,
)
from odessy.models.usage import UsageLedger
from odessy.runtime.speech import SpeechStream, current_speech, same_text, speech_scope
from odessy.runtime.types import FinalResponse, ToolObservation, ToolRequest
from odessy.voice.session import STOPPED_TEXT, SessionOptions, VoiceSession


def _sse(*chunks: dict[str, Any]) -> list[bytes]:
    return [f"data: {json.dumps(chunk)}\n".encode() for chunk in chunks] + [b"data: [DONE]\n"]


def _delta(**delta: Any) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": delta}]}


def _records(path: Path) -> list[dict[str, Any]]:
    return read_records(path, 0)


@pytest.fixture
def egress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "egress.jsonl"
    monkeypatch.setenv("ODESSY_EGRESS_FILE", str(path))
    return path


# --- sentences ------------------------------------------------------------------------


def test_sentences_are_emitted_as_they_complete() -> None:
    said: list[str] = []
    stream = SpeechStream(said.append)
    stream.begin()
    for piece in ["今天天气", "很好。明天", "会下雨！Pi is 3.", "14 today. And"]:
        stream.feed(piece)
    assert said == ["今天天气很好。", "明天会下雨！", "Pi is 3.14 today."]
    stream.end()
    assert said[-1] == "And"
    assert stream.last_text == "今天天气很好。明天会下雨！Pi is 3.14 today. And"
    assert stream.emitted == 4


def test_a_long_sentence_is_split_at_a_comma() -> None:
    said: list[str] = []
    stream = SpeechStream(said.append, max_chars=20)
    stream.begin()
    stream.feed("one two three, four five six seven eight")
    assert said == ["one two three,", "four five six"]


def test_a_tool_call_holds_the_rest_of_the_reply() -> None:
    said: list[str] = []
    stream = SpeechStream(said.append)
    stream.begin()
    stream.feed("Let me check. The disk")
    stream.hold()
    stream.feed(" is full.")
    stream.end()
    assert said == ["Let me check."]
    assert stream.last_text == ""  # A reply with a tool call is never the final answer.


def test_same_text_ignores_whitespace_only() -> None:
    assert same_text("It is  noon.", "It is noon.")
    assert not same_text("", "")
    assert not same_text("It is noon.", "It is one.")


# --- streaming: OpenAI-compatible --------------------------------------------------------


def test_a_stream_is_assembled_into_the_plain_response_shape() -> None:
    said: list[str] = []
    stream = SpeechStream(said.append)
    stream.begin()
    data = read_chat_stream(
        _sse(
            _delta(content="Checking now. "),
            _delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "time__", "arguments": ""}}]),
            _delta(tool_calls=[{"index": 0, "function": {"name": "now", "arguments": "{}"}}]),
            _delta(content="ignored"),
            {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4}},
        ),
        stream,
    )
    (choice,) = data["choices"]
    assert choice["message"]["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "time__now", "arguments": "{}"}}
    ]
    assert data["usage"] == {"prompt_tokens": 9, "completion_tokens": 4}
    assert said == ["Checking now."]


def test_a_stream_error_chunk_is_reported() -> None:
    with pytest.raises(RuntimeError, match="stream failed"):
        read_chat_stream(_sse({"error": {"message": "overloaded"}}), SpeechStream(lambda _: None))


@pytest.mark.parametrize('delta', [
    _delta(content='An unfinished answer'),
    _delta(tool_calls=[{'index': 0, 'id': 'c1', 'function': {'name': 'time__now', 'arguments': '{}'}}]),
])
def test_eof_without_completion_is_a_transient_error(delta) -> None:
    with pytest.raises(_TransientCloudError, match='incomplete'):
        read_chat_stream(_sse(delta)[:-1], SpeechStream(lambda _: None))


def test_finish_reason_allows_completion_without_done_marker() -> None:
    data = read_chat_stream(_sse(_delta(content='Finished.'),
        {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})[:-1],
        SpeechStream(lambda _: None))
    assert data['choices'][0]['message']['content'] == 'Finished.'


class _StreamingOpenAI(CloudModel):
    def __init__(self, streams: list[Any], **kwargs: Any) -> None:
        super().__init__(provider="deepseek", model="m", api_key="k", usage=UsageLedger(), **kwargs)
        self.streams = list(streams)
        self.payloads: list[dict[str, Any]] = []
        self.plain = 0
        self._sleep = lambda seconds: None

    def _post_chat_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.plain += 1
        return {"choices": [{"message": {"role": "assistant", "content": "plain answer"}}], "usage": {}}

    def _post_chat_stream_once(self, payload: dict[str, Any], speech: SpeechStream) -> dict[str, Any]:
        self.payloads.append(payload)
        step = self.streams.pop(0)
        if callable(step):
            return step(speech)
        return read_chat_stream(step, speech)


def test_a_voice_turn_streams_and_speaks_the_answer() -> None:
    said: list[str] = []
    model = _StreamingOpenAI([_sse(_delta(content="It is noon. "), _delta(content="Enjoy lunch."))])
    with speech_scope(SpeechStream(said.append)):
        action = model.propose("time?")
    assert action == FinalResponse(text="It is noon. Enjoy lunch.")
    assert said == ["It is noon.", "Enjoy lunch."]
    assert model.payloads[0]["stream"] is True
    assert model.payloads[0]["stream_options"] == {"include_usage": True}


def test_without_a_speech_stream_nothing_is_streamed() -> None:
    model = _StreamingOpenAI([])
    assert model.propose("time?") == FinalResponse(text="plain answer")
    assert model.plain == 1 and model.payloads == []


def test_a_stream_that_failed_before_speaking_is_retried() -> None:
    def fail(speech: SpeechStream) -> Any:
        speech.feed("half a sente")  # No sentence ended, so nothing was spoken.
        raise _TransientCloudError("reset")

    said: list[str] = []
    model = _StreamingOpenAI([fail, _sse(_delta(content="Fine."))])
    with speech_scope(SpeechStream(said.append)):
        assert model.propose("hi") == FinalResponse(text="Fine.")
    assert said == ["Fine."]


def test_a_stream_that_failed_after_speaking_is_not_repeated() -> None:
    def fail(speech: SpeechStream) -> Any:
        speech.feed("First sentence. ")
        raise _TransientCloudError("reset")

    said: list[str] = []
    model = _StreamingOpenAI([fail, _sse(_delta(content="Never sent."))])
    with speech_scope(SpeechStream(said.append)), pytest.raises(CloudUnavailableError):
        model.propose("hi")
    assert said == ["First sentence."]
    assert len(model.payloads) == 1


def test_spent_retries_raise_unavailable() -> None:
    class Down(CloudModel):
        def _post_chat_once(self, payload: dict[str, Any]) -> dict[str, Any]:
            raise _TransientCloudError("503")

    model = Down(provider="deepseek", model="m", api_key="k", usage=UsageLedger(), retries=1, _sleep=lambda s: None)
    with pytest.raises(CloudUnavailableError, match="503"):
        model.propose("hi")


# --- streaming: Claude -----------------------------------------------------------------


class _FakeStream:
    def __init__(self, events: list[Any], final: Any) -> None:
        self.events, self.final = events, final

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def __iter__(self):
        return iter(self.events)

    def get_final_message(self) -> Any:
        return self.final


class _APIStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code
        self.message = f"status {status_code}"


class _APIConnectionError(Exception):
    pass


_FAKE_ANTHROPIC = SimpleNamespace(APIStatusError=_APIStatusError, APIConnectionError=_APIConnectionError)


def test_claude_streams_text_before_a_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cloud, "_import_anthropic", lambda: _FAKE_ANTHROPIC)
    final = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="Let me look. "),
            SimpleNamespace(type="tool_use", id="t1", name="time__now", input={}),
        ],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=5, output_tokens=3),
    )
    events = [
        SimpleNamespace(type="text", text="Let me look. "),
        SimpleNamespace(type="content_block_start", content_block=SimpleNamespace(type="tool_use")),
        SimpleNamespace(type="text", text="never spoken"),
    ]
    calls: list[dict[str, Any]] = []

    def stream(**params: Any) -> _FakeStream:
        calls.append(params)
        return _FakeStream(events, final)

    client = SimpleNamespace(messages=SimpleNamespace(stream=stream), beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    model = CloudModel(provider="claude", model="claude-opus-5-5", api_key="k", usage=UsageLedger())
    model._client = client
    said: list[str] = []
    with speech_scope(SpeechStream(said.append)):
        action = model.propose("time?")
    assert action == ToolRequest("time.now", {})
    assert said == ["Let me look."]
    assert calls and calls[0]["model"] == "claude-opus-5-5"


@pytest.mark.parametrize(
    ("error", "unavailable"),
    [(_APIStatusError(529), True), (_APIStatusError(429), True), (_APIStatusError(400), False), (_APIConnectionError("x"), True)],
)
def test_claude_errors_are_classified(error: Exception, unavailable: bool) -> None:
    mapped = cloud._claude_error(_FAKE_ANTHROPIC, error)
    assert isinstance(mapped, RuntimeError)
    assert isinstance(mapped, CloudUnavailableError) is unavailable


# --- egress log ------------------------------------------------------------------------


class _Answers(CloudModel):
    def __init__(self, replies: list[Any], **kwargs: Any) -> None:
        super().__init__(provider="deepseek", model="deepseek-chat", api_key="k", usage=UsageLedger(), **kwargs)
        self.replies = list(replies)

    def _post_chat_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _answer(text: str = "", call: tuple[str, str] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if call:
        message["tool_calls"] = [{"id": call[0], "type": "function", "function": {"name": call[1], "arguments": "{}"}}]
    return {"choices": [{"message": message}], "usage": {"prompt_tokens": 12, "completion_tokens": 3, "prompt_cache_hit_tokens": 2}}


def test_every_request_is_logged_without_content(egress: Path) -> None:
    from odessy.runtime.types import ConversationContext, ConversationTurn

    model = _Answers([_answer(call=("c1", "time__now")), _answer("It is noon.")])

    action = model.propose("my private question", context=ConversationContext((ConversationTurn("earlier question", "earlier answer"),)))
    model.respond_to_observation("my private question", ToolObservation(request=action, result={"status": "ok", "iso_time": "12:00"}))

    first, second = _records(egress)
    assert first["event"] == "model_request"
    assert (first["provider"], first["model"], first["host"]) == ("deepseek", "deepseek-chat", "api.deepseek.com")
    assert first["sent"]["history_turns"] == 1 and first["sent"]["tool_results"] == 0
    assert first["tokens"] == {"prompt": 12, "cached": 2, "completion": 3}
    assert first["outcome"] == "ok" and first["streamed"] is False
    assert second["sent"]["tool_results"] == 1
    raw = egress.read_text(encoding="utf-8")
    for content in ("private question", "earlier question", "12:00", "It is noon"):
        assert content not in raw


def test_a_failed_request_is_logged_as_an_error(egress: Path) -> None:
    model = _Answers([RuntimeError("bad request")])
    with pytest.raises(RuntimeError):
        model.propose("hi")
    (record,) = _records(egress)
    assert record["outcome"] == "error" and record["error_type"] == "RuntimeError"


def test_an_empty_setting_turns_the_log_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODESSY_EGRESS_FILE", "")
    assert egress_file() is None
    _Answers([_answer("ok")]).propose("hi")
    assert not list(tmp_path.iterdir())


def test_the_egress_command_prints_recent_records(egress: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _Answers([_answer("ok")]).propose("hi")
    from odessy.cli import main

    main(["egress", "--limit", "5"])
    out = capsys.readouterr().out
    assert "deepseek/deepseek-chat @ api.deepseek.com" in out
    assert "12 in / 3 out tokens" in out


def test_a_failover_record_formats_plainly() -> None:
    line = format_record({"time": "2026-10-21T10:00:00.000+00:00", "event": "failover", "from": "claude", "to": "lfm", "to_local": True, "error_type": "CloudUnavailableError"})
    assert "failover claude -> lfm (local; CloudUnavailableError)" in line


# --- failover configuration --------------------------------------------------------------


def test_failover_lists_parse_and_validate() -> None:
    config = FailoverConfig.from_env("claude", {"ODESSY_CLOUD_FAILOVER": " DeepSeek, qwen ,lfm", "ODESSY_CLOUD_FAILOVER_COOLDOWN": "5"})
    assert config == FailoverConfig(("deepseek", "qwen", "lfm"), 5.0)
    assert FailoverConfig.from_env("claude", {}) is None
    router = FailoverConfig.from_env("claude", {"ODESSY_CLOUD_FAILOVER": "lfm"}, allow_local=False)
    assert router is None


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("gemini", "cannot be a fallback"),
        ("custom", "cannot be a fallback"),
        ("claude", "listed twice or is the selected provider"),
        ("qwen,qwen", "listed twice"),
    ],
)
def test_bad_failover_lists_are_refused(raw: str, message: str) -> None:
    with pytest.raises(CloudModelConfigError, match=message):
        FailoverConfig.from_env("claude", {"ODESSY_CLOUD_FAILOVER": raw})


def test_a_cost_budget_cannot_span_providers() -> None:
    env = {"ODESSY_CLOUD_FAILOVER": "deepseek", "ODESSY_CLOUD_BUDGET_COST": "5"}
    with pytest.raises(CloudModelConfigError, match="BUDGET_TOKENS"):
        FailoverConfig.from_env("claude", env)
    # A local fallback costs nothing, so the budget still holds.
    assert FailoverConfig.from_env("claude", {**env, "ODESSY_CLOUD_FAILOVER": "lfm"}) is not None


@pytest.mark.parametrize("raw", ["soon", "-1", "nan", "inf"])
def test_a_bad_cooldown_is_refused(raw: str) -> None:
    with pytest.raises(CloudModelConfigError, match="COOLDOWN"):
        FailoverConfig.from_env("claude", {"ODESSY_CLOUD_FAILOVER": "qwen", "ODESSY_CLOUD_FAILOVER_COOLDOWN": raw})


def test_a_fallback_uses_its_own_preset_and_model() -> None:
    env = {
        "ODESSY_CLOUD_MODEL": "claude-opus-5-5",
        "ODESSY_CLOUD_BASE_URL": "https://proxy.example",
        "ODESSY_CLOUD_API_KEY": "claude-key",
        "ODESSY_CLOUD_QWEN_MODEL": "qwen-max",
        "ODESSY_CLOUD_TIMEOUT": "30",
    }
    assert fallback_env("qwen", env) == {"ODESSY_CLOUD_QWEN_MODEL": "qwen-max", "ODESSY_CLOUD_TIMEOUT": "30", "ODESSY_CLOUD_MODEL": "qwen-max"}


def test_the_builder_wraps_only_when_fallbacks_are_configured() -> None:
    base = {"ANTHROPIC_API_KEY": "a", "DEEPSEEK_API_KEY": "d"}
    assert isinstance(build_cloud_model_with_failover("claude", base), CloudModel)

    built: list[str] = []
    model = build_cloud_model_with_failover(
        "claude",
        {**base, "ODESSY_CLOUD_FAILOVER": "deepseek,ollama"},
        local_factory=lambda name: built.append(name) or SimpleNamespace(model="gemma3:1b"),
    )
    assert isinstance(model, FailoverModel)
    assert [name for name, _ in model.candidates] == ["claude", "deepseek", "ollama"]
    assert model.chain() == "claude/claude-opus-5-5 -> deepseek/deepseek-flash -> ollama/gemma3:1b"
    assert built == ["ollama"]


def test_a_fallback_without_a_key_fails_at_startup() -> None:
    with pytest.raises(CloudModelConfigError, match="fallback qwen"):
        build_cloud_model_with_failover("claude", {"ANTHROPIC_API_KEY": "a", "ODESSY_CLOUD_FAILOVER": "qwen"})


# --- failover behaviour --------------------------------------------------------------------


class _Scripted:
    def __init__(self, name: str, steps: list[Any]) -> None:
        self.model = name
        self.steps = list(steps)
        self.proposals: list[tuple[str, Any]] = []
        self.observations = 0

    def propose(self, user_text: str, *, context: Any = None) -> Any:
        self.proposals.append((user_text, context))
        return self._next()

    def respond_to_observation(self, user_text: str, observation: Any) -> Any:
        self.observations += 1
        return self._next()

    def _next(self) -> Any:
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _failover(*models: _Scripted, clock: _Clock | None = None) -> tuple[FailoverModel, list[str]]:
    announced: list[str] = []
    return (
        FailoverModel([(model.model, model) for model in models], cooldown=60, announce=announced.append, clock=clock or _Clock()),
        announced,
    )


def test_an_unavailable_provider_moves_the_turn_and_says_so(egress: Path) -> None:
    primary = _Scripted("claude", [CloudUnavailableError("529 overloaded")])
    backup = _Scripted("deepseek", [FinalResponse("from backup")])
    model, announced = _failover(primary, backup)

    assert model.propose("hi") == FinalResponse("from backup")
    assert model.provider == "deepseek"
    (message,) = announced
    assert "claude is unavailable (529 overloaded)" in message and "deepseek" in message
    (record,) = _records(egress)
    assert record == {**record, "event": "failover", "from": "claude", "to": "deepseek", "to_local": True}


def test_a_failed_provider_is_skipped_until_its_cooldown_ends() -> None:
    clock = _Clock()
    primary = _Scripted("claude", [CloudUnavailableError("down"), FinalResponse("primary again")])
    backup = _Scripted("deepseek", [FinalResponse("one"), FinalResponse("two")])
    model, _ = _failover(primary, backup, clock=clock)

    model.propose("a")
    assert model.propose("b") == FinalResponse("two")
    assert len(primary.proposals) == 1
    clock.now += 61
    assert model.propose("c") == FinalResponse("primary again")


def test_cooldown_fallback_is_announced_and_logged_each_turn(egress: Path) -> None:
    primary = _Scripted('claude', [CloudUnavailableError('down')])
    backup = _Scripted('deepseek', [FinalResponse('one'), FinalResponse('two')])
    model, announced = _failover(primary, backup)
    model.propose('a')
    announced.clear()
    model.propose('b')
    assert len(announced) == 1
    assert 'cooldown' in announced[0] and 'deepseek' in announced[0]
    assert len(_records(egress)) == 2


def test_other_errors_do_not_move_the_turn() -> None:
    primary = _Scripted("claude", [RuntimeError("401 invalid key")])
    backup = _Scripted("deepseek", [FinalResponse("never")])
    model, announced = _failover(primary, backup)
    with pytest.raises(RuntimeError, match="401"):
        model.propose("hi")
    assert backup.proposals == [] and announced == []


def test_a_failure_after_tools_ran_ends_the_turn() -> None:
    clock = _Clock()
    request = ToolRequest("time.now", {})
    primary = _Scripted("claude", [request, CloudUnavailableError("timeout")])
    backup = _Scripted("deepseek", [FinalResponse("next turn")])
    model, _ = _failover(primary, backup, clock=clock)

    assert model.propose("time?") == request
    with pytest.raises(CloudUnavailableError):
        model.respond_to_observation("time?", ToolObservation(request=request, result={"status": "ok"}))
    assert backup.proposals == []
    assert model.propose("again") == FinalResponse("next turn")


def test_when_everything_is_down_all_are_tried_in_order() -> None:
    clock = _Clock()
    primary = _Scripted("claude", [CloudUnavailableError("a"), CloudUnavailableError("c")])
    backup = _Scripted("deepseek", [CloudUnavailableError("b"), FinalResponse("recovered")])
    model, _ = _failover(primary, backup, clock=clock)
    with pytest.raises(CloudUnavailableError, match="b"):
        model.propose("one")
    assert model.propose("two") == FinalResponse("recovered")


def test_history_reaches_the_backend_that_answers() -> None:
    primary = _Scripted("claude", [CloudUnavailableError("down")])
    backup = _Scripted("deepseek", [FinalResponse("ok")])
    model, _ = _failover(primary, backup)
    model.propose("hi", context="history")
    assert backup.proposals == [("hi", "history")]

    router_child, _ = _failover(_Scripted("claude", [FinalResponse("ok")]), _Scripted("qwen", []))
    router_child.conversation_context = ({"role": "user", "content": "x"},)
    router_child.propose("hi")
    assert router_child.active.proposals == [("hi", ({"role": "user", "content": "x"},))]


# --- the voice session -----------------------------------------------------------------


class _NoFrames:
    frame_bytes = 1024

    def score(self, pcm: bytes) -> float:
        return 0.0

    def is_speech(self, pcm: bytes) -> bool:
        return False

    def reset(self) -> None:
        pass


def _session(run_turn: Any, spoken: list[str]) -> VoiceSession:
    async def speak(text: str) -> None:
        spoken.append(text)

    async def frames() -> bytes:
        return b"\x00" * 1280

    async def transcribe(pcm: bytes) -> str:
        return ""

    return VoiceSession(
        frames=frames,
        wake=_NoFrames(),
        gate=SimpleNamespace(accepts=lambda score, frames_since_activation: False),
        vad=_NoFrames(),
        transcribe=transcribe,
        speak=speak,
        stop_speaking=lambda: None,
        run_turn=run_turn,
        classify_blank=lambda text: None,
        options=SessionOptions(),
        stream_speech=True,
    )


def _model_thread(*pieces: str) -> None:
    """What a streaming adapter does on the generation thread."""
    speech = current_speech()
    assert speech is not None
    speech.begin()
    for piece in pieces:
        speech.feed(piece)
    speech.end()


@pytest.mark.anyio
async def test_streamed_sentences_are_spoken_once() -> None:
    spoken: list[str] = []

    async def run_turn(text: str, stop: asyncio.Event, context: Any) -> str:
        await asyncio.to_thread(_model_thread, "It is noon. ", "Enjoy lunch.")
        return "It is noon. Enjoy lunch."

    session = _session(run_turn, spoken)
    await session._run("time?")
    assert spoken == ["It is noon.", "Enjoy lunch."]
    assert session.state == "LISTENING"


@pytest.mark.anyio
async def test_a_different_answer_is_still_spoken() -> None:
    spoken: list[str] = []

    async def run_turn(text: str, stop: asyncio.Event, context: Any) -> str:
        await asyncio.to_thread(_model_thread, "Let me check. ")
        return "64-call turn limit reached."

    session = _session(run_turn, spoken)
    await session._run("do it")
    assert spoken == ["Let me check.", "64-call turn limit reached."]


@pytest.mark.anyio
async def test_a_sentence_still_in_flight_is_not_overtaken_by_the_end_of_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    spoken: list[str] = []
    loop = asyncio.get_running_loop()
    # The model thread's hand-off arrives after the turn has returned, as it can on a
    # busy machine; the session must still speak it before closing the turn.
    monkeypatch.setattr(loop, "call_soon_threadsafe", lambda callback, *args: loop.call_later(0.05, callback, *args))

    async def run_turn(text: str, stop: asyncio.Event, context: Any) -> str:
        _model_thread("Let me check. ")
        return "64-call turn limit reached."

    session = _session(run_turn, spoken)
    await session._run("do it")
    assert spoken == ["Let me check.", "64-call turn limit reached."]


@pytest.mark.anyio
async def test_a_stopped_turn_drops_the_rest_and_says_it_stopped() -> None:
    spoken: list[str] = []

    async def run_turn(text: str, stop: asyncio.Event, context: Any) -> str:
        stop.set()
        await asyncio.to_thread(_model_thread, "This is never heard. ")
        return "This is never heard."

    session = _session(run_turn, spoken)
    await session._run("long task")
    assert spoken == [STOPPED_TEXT]


@pytest.mark.anyio
async def test_without_stream_speech_no_stream_is_set() -> None:
    spoken: list[str] = []
    seen: list[Any] = []

    async def run_turn(text: str, stop: asyncio.Event, context: Any) -> str:
        seen.append(await asyncio.to_thread(current_speech))
        return "answer"

    session = _session(run_turn, spoken)
    session.stream_speech = False
    await session._run("hi")
    assert seen == [None] and spoken == ["answer"]


def test_a_consulted_peer_is_never_spoken() -> None:
    from odessy.collaboration.config import CollaborationConfig, CollaborationSettings, ParticipantProfile
    from odessy.collaboration.participants import CollaborationService

    seen: list[Any] = []
    config = CollaborationConfig(CollaborationSettings(enabled=True), {"reviewer": ParticipantProfile("reviewer", "ollama", model="test")})
    service = CollaborationService(config, adapters={"reviewer": lambda profile, question, context: seen.append(current_speech()) or "advice"})
    with speech_scope(SpeechStream(lambda sentence: None)):
        service.consult(participant="reviewer", question="review this", explicit=True)
    assert seen == [None]


def test_doctor_shows_the_failover_chain_and_the_egress_log() -> None:
    from odessy.config.doctor import _model_checks

    env = {
        "ANTHROPIC_API_KEY": "a",
        "DEEPSEEK_API_KEY": "d",
        "ODESSY_CLOUD_FAILOVER": "deepseek,lfm",
        "ODESSY_EGRESS_FILE": "",
    }
    summaries = [check.summary for check in _model_checks("claude", env, probes=None)]
    assert "failover: claude/claude-opus-5-5 -> deepseek/deepseek-flash -> lfm/local lfm (API keys found; not contacted)" in summaries
    assert summaries[-1].startswith("egress log off")

    broken = _model_checks("claude", {**env, "ODESSY_CLOUD_FAILOVER": "qwen"}, probes=None)
    assert any(check.status == "fail" and "fallback qwen" in check.summary for check in broken)
