"""Bounded turns: tool-result compaction, conversation caching, call limits, final call."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from odessy.authority.egress import read_records
from odessy.authority.policy import OdessyPolicy
from odessy.cli import _build_agent, default_max_model_calls
from odessy.models.cloud_model import (
    CloudModel,
    _COMPACT_MARK,
    _FINAL_CALL_MESSAGE,
    build_cloud_model,
    compact_result,
)
from odessy.models.fake_model import FakeModel
from odessy.models.usage import UsageLedger
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.runtime.types import FinalResponse, ToolObservation, ToolRequest
from odessy.tools.mcp_server import build_odessy_mcp_server


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


BIG = "x" * 9000


def _observe(model: CloudModel, request: ToolRequest, payload: str) -> Any:
    return model.respond_to_observation("task", ToolObservation(request, {"status": "ok", "content": payload}))


class _Claude(CloudModel):
    def __init__(self, responses: list[Any], **kwargs: Any) -> None:
        super().__init__(provider="claude", model="claude-opus-5-5", api_key="k", usage=UsageLedger(), **kwargs)
        self.responses = list(responses)
        self.params: list[dict[str, Any]] = []

    def _create_message(self, params: dict[str, Any]) -> Any:
        # Deep copy what was sent: later requests mutate the stored conversation.
        self.params.append(json.loads(json.dumps(params, default=lambda o: getattr(o, "__dict__", str(o)))))
        return self.responses.pop(0)


def _tool(call_id: str) -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id=call_id, name="time__now", input={})],
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def _text(text: str) -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


class _OpenAI(CloudModel):
    def __init__(self, replies: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(provider="openai", model="m", api_key="k", usage=UsageLedger(), **kwargs)
        self.replies = list(replies)
        self.payloads: list[dict[str, Any]] = []

    def _post_chat_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(json.loads(json.dumps(payload)))
        return self.replies.pop(0)


def _call(call_id: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": "time__now", "arguments": "{}"}}
        ]}}],
        "usage": {},
    }


def _answer(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": text}}], "usage": {}}


def _tool_contents(payload: dict[str, Any]) -> list[str]:
    return [message["content"] for message in payload["messages"] if message["role"] == "tool"]


# --- compaction ---------------------------------------------------------------------------


def test_a_compacted_result_keeps_its_start_and_end_and_says_what_was_cut() -> None:
    content = "HEAD" + "m" * 3000 + "TAIL"
    short = compact_result(content)
    assert short.startswith("HEAD") and short.endswith("TAIL")
    assert _COMPACT_MARK in short and "2208 of 3008 characters omitted" in short
    assert "Call the tool again" in short


def test_results_under_the_budget_are_sent_whole() -> None:
    model = _OpenAI([_call("c1"), _call("c2"), _answer("done")], context_chars=24000)
    request = model.propose("task")
    _observe(model, request, "a" * 5000)
    _observe(model, request, "b" * 5000)
    assert all(_COMPACT_MARK not in content for content in _tool_contents(model.payloads[-1]))
    assert model.last_turn_diagnostics.compacted_results == 0


def test_the_oldest_results_are_compacted_and_the_latest_stay_whole() -> None:
    model = _OpenAI([_call("c1"), _call("c2"), _call("c3"), _answer("done")], context_chars=20000)
    request = model.propose("task")
    for payload in (BIG, BIG, BIG):
        _observe(model, request, payload)
    first, second, third = _tool_contents(model.payloads[-1])
    assert _COMPACT_MARK in first
    assert _COMPACT_MARK not in second and _COMPACT_MARK not in third
    diagnostics = model.last_turn_diagnostics
    assert diagnostics.compacted_results == 1 and diagnostics.compacted_chars > 8000


def test_a_compacted_result_never_changes_again() -> None:
    model = _OpenAI([_call(f"c{i}") for i in range(1, 5)] + [_answer("done")], context_chars=20000)
    request = model.propose("task")
    for payload in (BIG, BIG, BIG):
        _observe(model, request, payload)
    before = _tool_contents(model.payloads[-1])[0]
    _observe(model, request, BIG)
    after = _tool_contents(model.payloads[-1])
    assert after[0] == before  # Same bytes: the cached prefix still matches.
    assert _COMPACT_MARK in after[1]


def test_keep_zero_compacts_everything_needed() -> None:
    model = _OpenAI([_call("c1"), _answer("done")], context_chars=1000, keep_recent_results=0)
    request = model.propose("task")
    _observe(model, request, BIG)
    assert _COMPACT_MARK in _tool_contents(model.payloads[-1])[0]


def test_compaction_can_be_turned_off() -> None:
    model = _OpenAI([_call("c1"), _call("c2"), _call("c3"), _answer("done")], context_chars=0)
    request = model.propose("task")
    for payload in (BIG, BIG, BIG):
        _observe(model, request, payload)
    assert all(_COMPACT_MARK not in content for content in _tool_contents(model.payloads[-1]))


def test_claude_compacts_tool_results_and_leaves_its_own_messages_alone() -> None:
    first, second, third = _tool("t1"), _tool("t2"), _tool("t3")
    model = _Claude([first, second, third, _text("done")], context_chars=20000)
    request = model.propose("task")
    for payload in (BIG, BIG, BIG):
        _observe(model, request, payload)
    assistant = [message for message in model._messages if message["role"] == "assistant"]
    assert [message["content"] for message in assistant][:3] == [first.content, second.content, third.content]
    assert all(message["content"] is reply.content for message, reply in zip(assistant, (first, second, third)))
    results = [block for message in model._messages if message["role"] == "user" and isinstance(message["content"], list)
               for block in message["content"]]
    assert _COMPACT_MARK in results[0]["content"]
    assert all(_COMPACT_MARK not in block["content"] for block in results[1:])


def test_the_egress_log_counts_compacted_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "egress.jsonl"
    monkeypatch.setenv("ODESSY_EGRESS_FILE", str(path))
    model = _OpenAI([_call("c1"), _call("c2"), _call("c3"), _answer("done")], context_chars=20000)
    request = model.propose("task")
    for payload in (BIG, BIG, BIG):
        _observe(model, request, payload)
    assert [record["sent"]["compacted"] for record in read_records(path, 0)] == [0, 0, 0, 1]


def test_settings_are_read_from_the_environment() -> None:
    model = build_cloud_model("deepseek", {"DEEPSEEK_API_KEY": "k", "ODESSY_CLOUD_CONTEXT_CHARS": "0", "ODESSY_CLOUD_KEEP_RECENT_RESULTS": "3"})
    assert (model.context_chars, model.keep_recent_results) == (0, 3)
    default = build_cloud_model("deepseek", {"DEEPSEEK_API_KEY": "k"})
    assert (default.context_chars, default.keep_recent_results) == (24000, 2)


# --- conversation caching (Claude) -----------------------------------------------------------


def test_claude_caches_the_conversation_from_the_second_request() -> None:
    model = _Claude([_tool("t1"), _text("done")])
    request = model.propose("task")
    model.respond_to_observation("task", ToolObservation(request, {"status": "ok"}))
    first, second = model.params
    assert "cache_control" not in json.dumps(first["messages"])
    last_block = second["messages"][-1]["content"][-1]
    assert last_block["cache_control"] == {"type": "ephemeral"}
    assert json.dumps(second).count("cache_control") == 3  # tools, system, last message
    assert "cache_control" not in str(model._messages)


def test_conversation_caching_follows_the_cache_switch() -> None:
    model = _Claude([_tool("t1"), _text("done")], prompt_cache=False)
    request = model.propose("task")
    model.respond_to_observation("task", ToolObservation(request, {"status": "ok"}))
    assert "cache_control" not in json.dumps(model.params)


# --- the final call ---------------------------------------------------------------------------


def test_claude_final_call_has_no_tools_and_asks_for_an_answer() -> None:
    model = _Claude([_tool("t1"), _text("Checked the time; nothing left.")])
    request = model.propose("task")
    model.prepare_final_call()
    action = model.respond_to_observation("task", ToolObservation(request, {"status": "ok"}))
    assert action == FinalResponse("Checked the time; nothing left.")
    final = model.params[1]
    assert final["tool_choice"] == {"type": "none"}
    blocks = final["messages"][-1]["content"]
    assert blocks[0]["type"] == "tool_result"  # Results first, as the API requires.
    assert blocks[-1]["type"] == "text" and blocks[-1]["text"] == _FINAL_CALL_MESSAGE
    assert model.params[0]["tool_choice"]["type"] == "auto"


def test_openai_final_call_has_no_tools_and_asks_for_an_answer() -> None:
    model = _OpenAI([_call("c1"), _answer("Done so far: one check.")])
    request = model.propose("task")
    model.prepare_final_call()
    model.respond_to_observation("task", ToolObservation(request, {"status": "ok"}))
    final = model.payloads[1]
    assert final["tool_choice"] == "none"
    assert final["messages"][-1] == {"role": "user", "content": _FINAL_CALL_MESSAGE}
    assert final["messages"][-2]["role"] == "tool"


def test_the_final_flag_lasts_one_request_and_a_new_turn_clears_it() -> None:
    model = _OpenAI([_answer("a"), _answer("b")])
    model.prepare_final_call()
    model.propose("new turn")
    assert model.payloads[0]["tool_choice"] == "auto"


class _Scripted:
    """A model that keeps asking for tools, and can be told its next call is the last."""

    def __init__(self, *, can_finish: bool) -> None:
        self.calls = 0
        self.final_requested = False
        if can_finish:
            self.prepare_final_call = self._prepare

    def _prepare(self) -> None:
        self.final_requested = True

    def propose(self, user_text: str) -> Any:
        self.calls += 1
        return ToolRequest("time.now", {})

    def respond_to_observation(self, user_text: str, observation: Any) -> Any:
        self.calls += 1
        if self.final_requested:
            return FinalResponse("Checked the time three times; the task is not finished.")
        return ToolRequest("time.now", {})


def _agent(tmp_path: Path, model: Any, limit: int) -> OdessyAgent:
    agent = OdessyAgent(
        model=model,
        policy=OdessyPolicy(workspace_root=tmp_path),
        mcp_server=ExecutionTarget(build_odessy_mcp_server(workspace_root=tmp_path)),
        confirm=False,
    )
    agent._max_model_calls = limit
    return agent


@pytest.mark.anyio
async def test_the_last_allowed_call_answers_instead_of_stopping(tmp_path: Path) -> None:
    model = _Scripted(can_finish=True)
    result = await _agent(tmp_path, model, 3).run_turn("keep checking the time")
    assert result.final_text == "Checked the time three times; the task is not finished."
    assert model.calls == 3
    events = [entry.event for entry in result.log]
    assert "final_call" in events and "continuation_budget_exhausted" not in events
    assert result.metrics.final_call is True


@pytest.mark.anyio
async def test_a_model_without_a_final_call_names_the_limit(tmp_path: Path) -> None:
    result = await _agent(tmp_path, _Scripted(can_finish=False), 3).run_turn("keep checking the time")
    assert "because this turn reached its limit on model calls (3, set by ODESSY_MAX_MODEL_CALLS)" in result.final_text
    assert "final_call" not in [entry.event for entry in result.log]


# --- call limits by mode -------------------------------------------------------------------


def test_typed_cloud_turns_get_more_calls_than_spoken_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert default_max_model_calls(cloud=True, mode="voice") == 8
    assert default_max_model_calls(cloud=True, mode="text") == 20
    assert default_max_model_calls(cloud=False, mode="text") == 4
    assert default_max_model_calls(cloud=True) == 8  # The evaluation suite's default.
    with pytest.raises(ValueError, match="turn mode"):
        default_max_model_calls(cloud=True, mode="chat")

    cloud = CloudModel(provider="deepseek", model="m", api_key="k")
    assert _build_agent(tmp_path, cloud, mode="text")._max_model_calls == 20
    assert _build_agent(tmp_path, cloud, mode="voice")._max_model_calls == 8
    assert _build_agent(tmp_path, FakeModel(), mode="text")._max_model_calls == 4
    monkeypatch.setenv("ODESSY_MAX_MODEL_CALLS", "12")
    assert _build_agent(tmp_path, cloud, mode="voice")._max_model_calls == 12


def test_wrappers_pass_the_final_call_on() -> None:
    from odessy.models.failover import FailoverModel

    seen: list[str] = []
    primary = SimpleNamespace(model="a", prepare_final_call=lambda: seen.append("primary"))
    backup = SimpleNamespace(model="b")
    FailoverModel([("claude", primary), ("deepseek", backup)], announce=lambda m: None).prepare_final_call()
    assert seen == ["primary"]
