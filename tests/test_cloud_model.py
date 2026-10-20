from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.cli import _build_model
from odessy.models.cloud_model import (
    CLOUD_PROVIDERS,
    CloudModel,
    CloudModelConfigError,
    build_cloud_model,
    cloud_tools_anthropic,
    cloud_tools_openai,
    decode_tool_name,
    encode_tool_name,
)
from odessy.tools.mcp_server import build_odessy_mcp_server
from odessy.authority.policy import OdessyPolicy
from odessy.tools.tool_contracts import TOOL_REQUEST_SCHEMAS
from odessy.runtime.types import FinalResponse, ToolObservation, ToolRequest

SECRET = "sk-" + "a" * 32


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# -- fakes -----------------------------------------------------------------


class QueuedOpenAI(CloudModel):
    """OpenAI-compatible adapter that answers from a queue instead of HTTP."""

    def __init__(self, replies: list[dict], provider: str = "deepseek") -> None:
        super().__init__(provider=provider, model="test-model", api_key="test-key")
        self.replies = list(replies)
        self.payloads: list[dict] = []

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Snapshot: the adapter keeps appending to the same message list.
        self.payloads.append(json.loads(json.dumps(payload)))
        if not self.replies:
            raise AssertionError("No queued reply")
        return self.replies.pop(0)


class QueuedClaude(CloudModel):
    """Claude adapter that answers from a queue instead of the SDK."""

    def __init__(self, replies: list[SimpleNamespace], model: str = "claude-opus-5-5", **kwargs) -> None:
        super().__init__(provider="claude", model=model, api_key="test-key", **kwargs)
        self.replies = list(replies)
        self.params: list[dict] = []

    def _create_message(self, params: dict[str, Any]) -> Any:
        snapshot = dict(params)
        snapshot["messages"] = [dict(m) for m in params["messages"]]
        self.params.append(snapshot)
        if not self.replies:
            raise AssertionError("No queued reply")
        return self.replies.pop(0)


def openai_reply(content: str = "", calls: list[tuple[str, str, Any]] | None = None) -> dict:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": args if isinstance(args, str) else json.dumps(args),
                },
            }
            for call_id, name, args in calls
        ]
    return {
        "choices": [{"message": message}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
    }


def claude_reply(
    text: str = "",
    tool: tuple[str, str, dict] | None = None,
    stop_reason: str = "end_turn",
) -> SimpleNamespace:
    content: list[SimpleNamespace] = []
    if text:
        content.append(SimpleNamespace(type="text", text=text))
    if tool:
        call_id, name, arguments = tool
        content.append(SimpleNamespace(type="tool_use", id=call_id, name=name, input=arguments))
        stop_reason = "tool_use"
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=200, output_tokens=20),
    )


# -- configuration ---------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "key_env", "base_url"),
    [
        ("claude", "ANTHROPIC_API_KEY", "https://api.anthropic.com"),
        ("openai", "OPENAI_API_KEY", "https://api.openai.com/v1"),
        ("deepseek", "DEEPSEEK_API_KEY", "https://api.deepseek.com/v1"),
        ("qwen", "DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("kimi", "MOONSHOT_API_KEY", "https://api.moonshot.cn/v1"),
    ],
)
def test_each_provider_builds_from_its_own_key(provider: str, key_env: str, base_url: str) -> None:
    model = build_cloud_model(provider, {key_env: "k-123"})

    assert model.provider == provider
    assert model.api_key == "k-123"
    assert model.base_url == base_url
    assert model.model == CLOUD_PROVIDERS[provider].default_model
    assert model.protocol == ("anthropic" if provider == "claude" else "openai")


def test_overrides_take_precedence_over_presets() -> None:
    model = build_cloud_model(
        "qwen",
        {
            "DASHSCOPE_API_KEY": "provider-key",
            "ODESSY_CLOUD_API_KEY": "override-key",
            "ODESSY_CLOUD_MODEL": "qwen-max",
            "ODESSY_CLOUD_BASE_URL": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "ODESSY_CLOUD_TIMEOUT": "30",
        },
    )

    assert model.api_key == "override-key"
    assert model.model == "qwen-max"
    assert model.base_url.startswith("https://dashscope-intl")
    assert model.timeout == 30


def test_missing_key_names_the_variable_to_set() -> None:
    with pytest.raises(CloudModelConfigError, match="MOONSHOT_API_KEY"):
        build_cloud_model("kimi", {})


def test_custom_provider_requires_base_url_and_model() -> None:
    with pytest.raises(CloudModelConfigError, match="ODESSY_CLOUD_BASE_URL"):
        build_cloud_model("custom", {"ODESSY_CLOUD_API_KEY": "k"})
    with pytest.raises(CloudModelConfigError, match="ODESSY_CLOUD_MODEL"):
        build_cloud_model(
            "custom", {"ODESSY_CLOUD_API_KEY": "k", "ODESSY_CLOUD_BASE_URL": "http://x/v1"}
        )


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(CloudModelConfigError, match="Unknown cloud provider"):
        build_cloud_model("gemini", {})


def test_api_key_is_not_in_repr() -> None:
    model = build_cloud_model("openai", {"OPENAI_API_KEY": "very-secret-key"})

    assert "very-secret-key" not in repr(model)


def test_cli_selects_cloud_backend_and_ignores_ollama_model_name(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.delenv("ODESSY_CLOUD_MODEL", raising=False)
    monkeypatch.delenv("ODESSY_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("ODESSY_CLOUD_BASE_URL", raising=False)

    model = _build_model(False, backend="deepseek", model_name="lfm2.5-thinking:1.2b-q8_0")

    assert isinstance(model, CloudModel)
    assert model.model == "deepseek-chat"


def test_cli_cloud_backend_from_env(monkeypatch) -> None:
    monkeypatch.setenv("ODESSY_MODEL_BACKEND", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("ODESSY_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("ODESSY_CLOUD_MODEL", raising=False)
    monkeypatch.delenv("ODESSY_CLOUD_BASE_URL", raising=False)

    model = _build_model(False)

    assert isinstance(model, CloudModel)
    assert model.provider == "claude"


def test_cli_missing_cloud_key_exits_with_message(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ODESSY_CLOUD_API_KEY", raising=False)

    with pytest.raises(SystemExit, match="OPENAI_API_KEY"):
        _build_model(False, backend="openai")


# -- tool schemas ----------------------------------------------------------


def test_tool_names_are_encoded_without_dots_and_round_trip() -> None:
    for name in TOOL_REQUEST_SCHEMAS:
        encoded = encode_tool_name(name)
        assert "." not in encoded
        assert decode_tool_name(encoded) == name


def test_cloud_models_are_offered_every_tool() -> None:
    openai_names = {tool["function"]["name"] for tool in cloud_tools_openai()}
    anthropic_tools = cloud_tools_anthropic()

    assert openai_names == {encode_tool_name(name) for name in TOOL_REQUEST_SCHEMAS}
    assert {tool["name"] for tool in anthropic_tools} == openai_names
    assert all(tool["input_schema"]["type"] == "object" for tool in anthropic_tools)


# -- OpenAI-compatible protocol --------------------------------------------


@pytest.mark.parametrize(
    ("provider", "effort"),
    [("deepseek", "low"), ("deepseek", "high"), ("deepseek", ""), ("openai", "low"), ("qwen", "low")],
)
def test_deepseek_effort_from_environment_is_sent_only_when_configured(monkeypatch, provider, effort) -> None:
    model = build_cloud_model(provider, {"ODESSY_CLOUD_API_KEY": "test-key", "ODESSY_CLOUD_EFFORT": effort})
    payloads = []

    def post_chat(payload):
        payloads.append(payload)
        return openai_reply(content="Hello.")

    monkeypatch.setattr(model, "_post_chat", post_chat)
    assert model.propose("hi") == FinalResponse(text="Hello.")

    if provider == "deepseek" and effort:
        assert payloads[0]["reasoning_effort"] == effort
    else:
        assert "reasoning_effort" not in payloads[0]


@pytest.mark.parametrize("reasoning", ["First inspect the status.", "", None])
def test_deepseek_reasoning_is_preserved_across_tool_calls(reasoning) -> None:
    first = openai_reply(calls=[("c1", "git__status", {})])
    first["choices"][0]["message"]["reasoning_content"] = reasoning
    second = openai_reply(calls=[("c2", "git__diff", {})])
    second["choices"][0]["message"]["reasoning_content"] = "Now inspect the diff."
    model = QueuedOpenAI([first, second, openai_reply(content="The port changed.")])
    prompt = "summarize my uncommitted changes"

    request = model.propose(prompt)
    request = model.respond_to_observation(prompt, ToolObservation(request, {"changed": ["a.py"]}))
    answer = model.respond_to_observation(prompt, ToolObservation(request, {"diff": "port changed"}))

    assert answer == FinalResponse(text="The port changed.")
    assistants = [m for m in model.payloads[2]["messages"] if m["role"] == "assistant"]
    assert [m["reasoning_content"] for m in assistants] == [reasoning, "Now inspect the diff."]
    assert [m["tool_calls"][0]["id"] for m in assistants] == ["c1", "c2"]


@pytest.mark.parametrize("with_tool", [True, False])
def test_deepseek_reasoning_is_preserved_during_repair(with_tool) -> None:
    first = openai_reply(calls=[("c1", "no__such__tool", {})] if with_tool else None)
    first["choices"][0]["message"]["reasoning_content"] = "I should check the time."
    model = QueuedOpenAI([first, openai_reply(calls=[("c2", "time__now", {})])])

    assert model.propose("time?") == ToolRequest(name="time.now", arguments={})
    assert model.payloads[1]["messages"][2]["reasoning_content"] == "I should check the time."


def test_openai_tool_call_becomes_tool_request() -> None:
    model = QueuedOpenAI([openai_reply(calls=[("call_1", "time__now", {})])])

    action = model.propose("what time is it?")

    assert action == ToolRequest(name="time.now", arguments={})
    payload = model.payloads[0]
    assert payload["model"] == "test-model"
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1] == {"role": "user", "content": "what time is it?"}
    assert len(payload["tools"]) == len(TOOL_REQUEST_SCHEMAS)
    assert "test-key" not in json.dumps(payload)


def test_openai_text_reply_becomes_final_response() -> None:
    model = QueuedOpenAI([openai_reply(content="Hello! How can I help?")])

    assert model.propose("hi") == FinalResponse(text="Hello! How can I help?")
    assert model.last_turn_diagnostics.model_call_count == 1
    assert model.last_turn_diagnostics.prompt_tokens == 100


def test_openai_invalid_arguments_are_repaired_once_through_a_tool_message() -> None:
    model = QueuedOpenAI(
        [
            openai_reply(calls=[("call_1", "calculator__eval", {"expression": 42})]),
            openai_reply(calls=[("call_2", "calculator__eval", {"expression": "6*7"})]),
        ]
    )

    action = model.propose("calculate 6 times 7")

    assert action == ToolRequest(name="calculator.eval", arguments={"expression": "6*7"})
    repair_messages = model.payloads[1]["messages"]
    assert repair_messages[-1]["role"] == "tool"
    assert repair_messages[-1]["tool_call_id"] == "call_1"
    assert "invalid" in repair_messages[-1]["content"]
    diagnostics = model.last_turn_diagnostics
    assert diagnostics.repair_triggered is True
    assert diagnostics.proposal_valid is False
    assert diagnostics.model_call_count == 2


def test_openai_repair_fails_after_exactly_one_retry() -> None:
    model = QueuedOpenAI(
        [
            openai_reply(calls=[("c1", "no__such__tool", {})]),
            openai_reply(calls=[("c2", "no__such__tool", {})]),
        ]
    )

    with pytest.raises(ValueError, match="Model repair failed after one retry"):
        model.propose("do something")
    assert model.replies == []


def test_openai_malformed_argument_json_is_repaired() -> None:
    model = QueuedOpenAI(
        [
            openai_reply(calls=[("c1", "time__now", "{not json")]),
            openai_reply(calls=[("c2", "time__now", "{}")]),
        ]
    )

    assert model.propose("time?") == ToolRequest(name="time.now", arguments={})


def test_cloud_model_chooses_the_machine_without_a_keyword_rule() -> None:
    # "my laptop" appears, but the user asked for this machine. A keyword rule forced
    # the laptop tool here; a cloud model reads the sentence and the system prompt.
    model = QueuedOpenAI([openai_reply(calls=[("c1", "terminal__run", {"command": "pwd"})])])

    action = model.propose("my laptop is off, so run pwd on this machine")

    assert action == ToolRequest(name="terminal.run", arguments={"command": "pwd"})
    assert model.last_turn_diagnostics.repair_triggered is False


def test_openai_observation_is_returned_as_tool_message_and_answered() -> None:
    model = QueuedOpenAI(
        [
            openai_reply(calls=[("call_1", "time__now", {})]),
            openai_reply(content="It is 3 PM."),
        ]
    )
    request = model.propose("what time is it?")

    action = model.respond_to_observation(
        "what time is it?",
        ToolObservation(request=request, result={"iso_time": "2026-10-16T15:00:00+08:00"}),
    )

    assert action == FinalResponse(text="It is 3 PM.")
    messages = model.payloads[1]["messages"]
    assert messages[-2]["role"] == "assistant"
    assert messages[-2]["tool_calls"][0]["id"] == "call_1"
    assert messages[-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"iso_time": "2026-10-16T15:00:00+08:00"}',
    }
    assert model.last_turn_diagnostics.final_generation_ms is not None
    assert model.last_turn_diagnostics.model_call_count == 2


def test_openai_observation_can_continue_with_another_tool() -> None:
    model = QueuedOpenAI(
        [
            openai_reply(calls=[("c1", "git__status", {})]),
            openai_reply(calls=[("c2", "git__diff", {})]),
        ]
    )
    request = model.propose("summarize my uncommitted changes")

    action = model.respond_to_observation(
        "summarize my uncommitted changes",
        ToolObservation(request=request, result={"changed": ["a.py"]}),
    )

    assert action == ToolRequest(name="git.diff", arguments={})


def test_openai_blank_final_answer_falls_back_to_summary() -> None:
    model = QueuedOpenAI([openai_reply(calls=[("c1", "time__now", {})]), openai_reply(content="")])
    request = model.propose("time?")

    action = model.respond_to_observation(
        "time?", ToolObservation(request=request, result={"iso_time": "noon"})
    )

    assert action == FinalResponse(text="The current time is noon.")


def test_openai_invalid_follow_up_call_gets_one_correction() -> None:
    # An invalid next call used to be dropped silently. It now gets one repair round,
    # like the first proposal.
    model = QueuedOpenAI(
        [
            openai_reply(calls=[("c1", "time__now", {})]),
            openai_reply(calls=[("c2", "no__such__tool", {})]),
            openai_reply(calls=[("c3", "calculator__eval", {"expression": "18*60 - 12*60"})]),
        ]
    )
    request = model.propose("minutes from noon to 18:00?")

    action = model.respond_to_observation(
        "minutes from noon to 18:00?", ToolObservation(request=request, result={"iso_time": "noon"})
    )

    assert action == ToolRequest(name="calculator.eval", arguments={"expression": "18*60 - 12*60"})
    repair = [m for m in model.payloads[2]["messages"] if m["role"] == "tool"][-1]
    assert repair["tool_call_id"] == "c2"
    assert "invalid" in repair["content"]


def test_openai_follow_up_still_invalid_after_correction_degrades_instead_of_raising() -> None:
    model = QueuedOpenAI(
        [
            openai_reply(calls=[("c1", "time__now", {})]),
            openai_reply(calls=[("c2", "no__such__tool", {})]),
            openai_reply(calls=[("c3", "no__such__tool", {})]),
        ]
    )
    request = model.propose("time?")

    action = model.respond_to_observation(
        "time?", ToolObservation(request=request, result={"iso_time": "noon"})
    )

    assert action == FinalResponse(text="The current time is noon.")


def test_parallel_calls_are_preserved_and_answered_in_model_order() -> None:
    from odessy.runtime.types import ToolBatch
    model = QueuedOpenAI([
        openai_reply(calls=[("c1", "git__status", {}), ("c2", "git__log", {})]),
        openai_reply(content="Clean tree."),
    ])
    batch = model.propose("git status and log")
    assert isinstance(batch, ToolBatch)
    observations = [ToolObservation(c.request, {"status": "ok"}, c.call_id) for c in batch.calls]
    model.respond_to_observations("git status and log", observations)
    tool_messages = [m for m in model.payloads[1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2"]
    assert all("Not executed" not in m["content"] for m in tool_messages)


def test_user_text_and_tool_output_are_redacted_before_sending() -> None:
    model = QueuedOpenAI(
        [
            openai_reply(calls=[("c1", "filesystem__read", {"path": ".env"})]),
            openai_reply(content="The file sets an API key."),
        ]
    )
    request = model.propose(f"is {SECRET} my key?")
    model.respond_to_observation(
        "read .env", ToolObservation(request, {"content": f"OPENAI_API_KEY={SECRET}"})
    )

    sent = json.dumps(model.payloads)
    assert SECRET not in sent
    assert "<redacted>" in sent


def test_observation_without_pending_call_restarts_with_inline_result() -> None:
    model = QueuedOpenAI([openai_reply(content="It is noon.")])

    action = model.respond_to_observation(
        "time?",
        ToolObservation(ToolRequest("time.now", {}), {"iso_time": "noon"}),
    )

    assert action == FinalResponse(text="It is noon.")
    messages = model.payloads[0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "Result of time.now" in messages[1]["content"]


def test_each_turn_starts_a_fresh_conversation() -> None:
    model = QueuedOpenAI([openai_reply(content="One."), openai_reply(content="Two.")])

    model.propose("first")
    model.propose("second")

    assert [m["role"] for m in model.payloads[1]["messages"]] == ["system", "user"]
    assert model.last_turn_diagnostics.model_call_count == 1


def test_provider_refusal_becomes_final_response() -> None:
    reply = openai_reply()
    reply["choices"][0]["message"]["refusal"] = "I can't help with that."
    model = QueuedOpenAI([reply], provider="openai")

    assert model.propose("bad request") == FinalResponse(text="I can't help with that request.")


# -- Claude protocol -------------------------------------------------------


def test_claude_tool_use_becomes_tool_request_with_expected_params() -> None:
    model = QueuedClaude([claude_reply(tool=("toolu_1", "time__now", {}))])

    action = model.propose("what time is it?")

    assert action == ToolRequest(name="time.now", arguments={})
    params = model.params[0]
    assert params["model"] == "claude-opus-5-5"
    assert params["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": False}
    assert params["fallbacks"] == "default"
    assert params["betas"] == ["server-side-fallback-2026-07-01"]
    assert "output_config" not in params
    assert params["messages"] == [{"role": "user", "content": "what time is it?"}]
    assert {tool["name"] for tool in params["tools"]} >= {"time__now", "laptop__terminal__run"}
    assert model.last_turn_diagnostics.prompt_tokens == 200


def test_claude_fallbacks_only_on_supported_models_and_can_be_disabled() -> None:
    haiku = QueuedClaude([claude_reply(text="Hi.")], model="claude-haiku-5-5")
    haiku.propose("hi")
    disabled = QueuedClaude([claude_reply(text="Hi.")], refusal_fallbacks=False)
    disabled.propose("hi")

    assert "fallbacks" not in haiku.params[0]
    assert "fallbacks" not in disabled.params[0]


def test_claude_effort_is_sent_when_configured() -> None:
    model = QueuedClaude([claude_reply(text="Hi.")], effort="low")
    model.propose("hi")

    assert model.params[0]["output_config"] == {"effort": "low"}


def test_claude_observation_is_a_tool_result_after_the_full_assistant_content() -> None:
    first = claude_reply(tool=("toolu_1", "git__status", {}))
    model = QueuedClaude([first, claude_reply(text="Your tree is clean.")])
    request = model.propose("git status")

    action = model.respond_to_observation("git status", ToolObservation(request, {"clean": True}))

    assert action == FinalResponse(text="Your tree is clean.")
    messages = model.params[1]["messages"]
    assert messages[1] == {"role": "assistant", "content": first.content}
    assert messages[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"clean": true}'}],
    }


def test_claude_refusal_becomes_final_response() -> None:
    model = QueuedClaude([claude_reply(stop_reason="refusal")])

    assert model.propose("bad request") == FinalResponse(text="I can't help with that request.")


def test_claude_invalid_tool_input_is_repaired_with_an_error_result() -> None:
    model = QueuedClaude(
        [
            claude_reply(tool=("toolu_1", "calculator__eval", {})),
            claude_reply(tool=("toolu_2", "calculator__eval", {"expression": "1+1"})),
        ]
    )

    action = model.propose("calculate 1+1")

    assert action == ToolRequest(name="calculator.eval", arguments={"expression": "1+1"})
    result_block = model.params[1]["messages"][-1]["content"][0]
    assert result_block["tool_use_id"] == "toolu_1"
    assert result_block["is_error"] is True


# -- authority boundary ----------------------------------------------------


@pytest.mark.anyio
async def test_cloud_model_runs_through_policy_and_mcp() -> None:
    model = QueuedOpenAI(
        [
            openai_reply(calls=[("c1", "time__now", {})]),
            openai_reply(content="It is quarter to midnight."),
        ]
    )
    agent = OdessyAgent(
        model=model,
        policy=OdessyPolicy(workspace_root=Path.cwd(), allowed_tools={"time.now"}),
        mcp_server=ExecutionTarget(
            build_odessy_mcp_server(clock=lambda: "2026-08-15T23:45:00+08:00")
        ),
    )

    result = await agent.run_turn("What time is it?")

    assert result.final_text == "It is quarter to midnight."
    assert result.tool_request == ToolRequest(name="time.now", arguments={})
    assert result.metrics.tool_called is True
    assert result.metrics.model_call_count == 2


@pytest.mark.anyio
async def test_cloud_model_cannot_authorize_a_denied_tool() -> None:
    model = QueuedOpenAI([openai_reply(calls=[("c1", "terminal__run", {"command": "pwd"})])])
    agent = OdessyAgent(
        model=model,
        policy=OdessyPolicy(workspace_root=Path.cwd(), allowed_tools={"time.now"}),
        mcp_server=ExecutionTarget(build_odessy_mcp_server()),
    )

    with pytest.raises(PermissionError, match="terminal.run"):
        await agent.run_turn("run pwd")
