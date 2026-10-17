from __future__ import annotations

import os

import pytest

from odessy.cli import _build_model
from odessy.lfm_model import LlamaCppLFMModel, normalize_ollama_chat_response
from odessy.ollama_model import _OllamaToolsUnsupported, _looks_like_generic_final_response
from odessy.types import FinalResponse, ToolObservation, ToolRequest


class QueuedLFM(LlamaCppLFMModel):
    """Deterministic LFM fake that bypasses the HTTP layer."""

    def __init__(self, outputs: list[dict]) -> None:
        object.__setattr__(self, "outputs", list(outputs))
        object.__setattr__(self, "calls", [])

    def _chat(self, messages, tools=None):
        self.calls.append({"messages": messages, "tools": tools})
        if not self.outputs:
            raise AssertionError("No queued LFM output available")
        return normalize_ollama_chat_response(self.outputs.pop(0))


def _assistant(content: str = "", tool_calls: list | None = None) -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls or [],
        }
    }


def _tool(content: str) -> dict:
    return _assistant(content=content)


# 1. backend initialization
def test_lfm_backend_initializes_with_explicit_endpoint() -> None:
    model = LlamaCppLFMModel(model="lfm2.5-thinking:1.2b-q8_0", endpoint="http://127.0.0.1:9000")

    assert model.model == "lfm2.5-thinking:1.2b-q8_0"
    assert model.endpoint == "http://127.0.0.1:9000"
    assert model.temperature == 0.0


# 2. endpoint configuration via the CLI/env selection path
def test_lfm_endpoint_configuration_from_env(monkeypatch) -> None:
    monkeypatch.setenv("ODESSY_MODEL_BACKEND", "llama_cpp")
    monkeypatch.setenv("ODESSY_MODEL_NAME", "lfm2.5-thinking:1.2b-q8_0")
    monkeypatch.setenv("ODESSY_MODEL_ENDPOINT", "http://127.0.0.1:11434")

    model = _build_model(use_fake_model=False)

    assert isinstance(model, LlamaCppLFMModel)
    assert model.endpoint == "http://127.0.0.1:11434"
    assert model.model == "lfm2.5-thinking:1.2b-q8_0"


def test_backend_flag_selects_lfm() -> None:
    model = _build_model(use_fake_model=False, backend="llama_cpp")

    assert isinstance(model, LlamaCppLFMModel)


# 3/4/5. successful request, parsing, and conversion into ToolRequest
def test_lfm_successful_tool_request_conversion() -> None:
    model = QueuedLFM(
        [_tool('{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}')]
    )

    action = model.propose("run pwd")

    assert isinstance(action, ToolRequest)
    assert action.name == "terminal.run"
    assert action.arguments == {"command": "pwd"}
    assert model.last_turn_diagnostics.proposal_valid is True
    assert model.last_turn_diagnostics.repair_triggered is False
    assert model.last_turn_diagnostics.proposal_generation_ms is not None
    assert model.last_turn_diagnostics.proposal_parsing_ms is not None


def test_lfm_native_tool_call_conversion() -> None:
    model = QueuedLFM(
        [
            _assistant(
                tool_calls=[
                    {
                        "function": {
                            "name": "time.now",
                            "arguments": {},
                        }
                    }
                ]
            )
        ]
    )

    action = model.propose("what time is it?")

    assert isinstance(action, ToolRequest)
    assert action.name == "time.now"
    assert model.calls[0]["tools"]


def test_lfm_native_no_tool_content_is_final_response() -> None:
    model = QueuedLFM([_assistant(content="Hello! How can I help?")])

    action = model.propose("hi")

    assert isinstance(action, FinalResponse)
    assert action.text == "Hello! How can I help?"
    assert len(model.calls) == 1


def test_lfm_tools_unsupported_falls_back_to_text_json() -> None:
    class UnsupportedThenText(QueuedLFM):
        def _chat(self, messages, tools=None):
            self.calls.append({"messages": messages, "tools": tools})
            if tools is not None:
                raise _OllamaToolsUnsupported("does not support tools")
            return normalize_ollama_chat_response(self.outputs.pop(0))

    model = UnsupportedThenText(
        [_tool('{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}')]
    )

    action = model.propose("run pwd")

    assert isinstance(action, ToolRequest)
    assert action.name == "terminal.run"
    assert len(model.calls) == 2
    assert model.last_turn_diagnostics.model_call_count == 2
    assert model.calls[0]["tools"]
    assert model.calls[1]["tools"] is None


def test_lfm_normalize_ollama_response() -> None:
    normalized = normalize_ollama_chat_response(
        {
            "message": {
                "role": "assistant",
                "content": "hi",
                "tool_calls": [],
                "thinking": "brief internal trace",
            },
            "total_duration": 2_500_000_000,
            "prompt_eval_duration": 500_000_000,
            "prompt_eval_count": 12,
            "eval_duration": 1_000_000_000,
            "eval_count": 5,
        }
    )

    assert normalized["message"]["content"] == "hi"
    assert normalized["message"]["tool_calls"] == []
    assert normalized["message"]["thinking"] == "brief internal trace"
    assert normalized["total_duration"] == 2_500_000_000
    assert normalized["eval_count"] == 5


def test_lfm_final_response_conversion() -> None:
    model = QueuedLFM([_tool('{"type":"final","text":"done"}')])

    action = model.propose("say hi")

    assert isinstance(action, FinalResponse)
    assert action.text == "done"


# 6/7. malformed output and bounded repair
def test_lfm_repairs_malformed_json_once() -> None:
    model = QueuedLFM(
        [
            _tool('{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"'),
            _tool('{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}'),
        ]
    )

    action = model.propose("run pwd")

    assert isinstance(action, ToolRequest)
    assert action.arguments == {"command": "pwd"}
    assert len(model.calls) == 2
    assert model.last_turn_diagnostics.proposal_valid is False
    assert model.last_turn_diagnostics.repair_triggered is True
    assert model.last_turn_diagnostics.repair_generation_ms is not None
    assert model.last_turn_diagnostics.model_call_count == 2


def test_lfm_records_token_counts_when_available() -> None:
    model = QueuedLFM(
        [
            {
                "message": {
                    "role": "assistant",
                    "content": "hello",
                    "tool_calls": [],
                },
                "prompt_eval_count": 12,
                "eval_count": 5,
            }
        ]
    )

    action = model.propose("hi")

    assert isinstance(action, FinalResponse)
    assert model.last_turn_diagnostics.prompt_tokens == 12
    assert model.last_turn_diagnostics.completion_tokens == 5


def test_lfm_records_ollama_latency_and_thinking_metadata() -> None:
    model = QueuedLFM(
        [
            {
                "message": {
                    "role": "assistant",
                    "content": "hello",
                    "thinking": "reasoning",
                    "tool_calls": [],
                },
                "total_duration": 2_500_000_000,
                "prompt_eval_duration": 500_000_000,
                "prompt_eval_count": 12,
                "eval_duration": 1_000_000_000,
                "eval_count": 5,
            }
        ]
    )

    action = model.propose("hi")

    assert isinstance(action, FinalResponse)
    diagnostics = model.last_turn_diagnostics
    assert diagnostics.model_total_duration_ms == pytest.approx(2500.0)
    assert diagnostics.prompt_eval_duration_ms == pytest.approx(500.0)
    assert diagnostics.prompt_eval_count == 12
    assert diagnostics.eval_duration_ms == pytest.approx(1000.0)
    assert diagnostics.eval_count == 5
    assert diagnostics.generation_tokens_per_sec == pytest.approx(5.0)
    assert diagnostics.model_call_count == 1
    assert diagnostics.thinking_exposed is True
    assert diagnostics.thinking_chars == len("reasoning")
    assert diagnostics.response_chars == len("hello")


def test_lfm_call_count_includes_final_observation_response() -> None:
    proposal = '{"type":"tool","name":"time.now","arguments":{}}'
    model = QueuedLFM(
        [
            _assistant(content=proposal),
            _assistant(content="It is noon."),
        ]
    )

    action = model.propose("what time is it?")
    assert isinstance(action, ToolRequest)
    model.respond_to_observation(
        "what time is it?",
        ToolObservation(
            request=action,
            result={"iso_time": "2026-09-20T12:00:00+00:00"},
        ),
    )

    assert model.last_turn_diagnostics.model_call_count == 2
    assert model.last_turn_diagnostics.response_chars == len(proposal) + len("It is noon.")


def test_lfm_observation_can_return_a_corrected_tool_request() -> None:
    """A rejection observation may be answered with another, corrected tool call."""

    model = QueuedLFM(
        [
            _assistant(
                tool_calls=[
                    {
                        "function": {
                            "name": "terminal.run",
                            "arguments": {
                                "command": "pwd",
                                "cwd": ".",
                                "timeout": 10,
                            },
                        }
                    }
                ]
            )
        ]
    )

    action = model.respond_to_observation(
        "run pwd",
        ToolObservation(
            request=ToolRequest(
                "terminal.run",
                {"command": "pwd", "cwd": ".", "timeout": 10, "bogus": 1},
            ),
            result={
                "status": "rejected",
                "reason": "Unexpected argument(s) for terminal.run: bogus",
            },
        ),
    )

    assert isinstance(action, ToolRequest)
    assert action.name == "terminal.run"
    assert action.arguments == {"command": "pwd", "cwd": ".", "timeout": 10}


def test_lfm_observation_with_text_still_returns_final_response() -> None:
    """A plain text answer keeps the historical summarisation path.

    Also a regression test for the removed ``len(normalized) < 12`` heuristic:
    "It is noon." is 11 characters and used to be discarded in favour of
    ``_fallback_observation_response``.
    """

    model = QueuedLFM([_assistant(content="It is noon.")])

    action = model.respond_to_observation(
        "what time is it?",
        ToolObservation(
            request=ToolRequest("time.now", {}),
            result={"iso_time": "2026-09-20T12:00:00+00:00"},
        ),
    )

    assert isinstance(action, FinalResponse)
    assert action.text == "It is noon."


@pytest.mark.parametrize(
    "answer",
    [
        "It is noon.",      # 11 chars: the exact case the old < 12 rule broke
        "12 files",
        "No matches.",
        "3",
        "pwd",
        "Command finished with status ok.",
    ],
)
def test_short_answers_are_not_treated_as_generic(answer: str) -> None:
    """Length is not a usable signal: terse replies are the normal voice case.

    Regression for the removed ``len(normalized) < 12`` heuristic, which replaced
    any short answer with the observation fallback.

    Note: bare acknowledgements such as "Done." and "Yes." remain classified as
    generic because they are in the explicit vocabulary below. That is a
    deliberate pre-existing policy choice, not the length bug.
    """

    assert _looks_like_generic_final_response(answer) is False


@pytest.mark.parametrize(
    "filler",
    [
        "",
        "   ",
        "ok",
        "OK.",
        "yes",
        "Yes!",
        "okay",
        "sure",
        "done",
        "Done.",
        "please wait",
        "Please wait.",
    ],
)
def test_bare_acknowledgements_are_treated_as_generic(filler: str) -> None:
    """The explicit filler vocabulary must still be caught."""

    assert _looks_like_generic_final_response(filler) is True


def test_lfm_observation_with_invalid_tool_call_degrades_instead_of_raising() -> None:
    """An unusable tool call must not raise into the continuation loop."""

    model = QueuedLFM(
        [
            _assistant(
                tool_calls=[{"function": {"name": "not.a.real.tool", "arguments": {}}}]
            )
        ]
    )

    action = model.respond_to_observation(
        "run pwd",
        ToolObservation(
            request=ToolRequest("terminal.run", {"command": "pwd"}),
            result={"status": "rejected", "reason": "Unexpected argument(s)"},
        ),
    )

    assert isinstance(action, FinalResponse)


def test_lfm_observation_advertises_tools() -> None:
    """Regression: the observation call must advertise tool definitions.

    Without an advertised list the model has no functions to call, so the
    response cannot carry a native `tool_call` and `_corrected_tool_request`
    always returns None. This is the one assertion that detects that: scripted
    doubles return ToolRequests regardless of whether tools were advertised, so
    every loop-level test passes while the feature is inert.
    """

    model = QueuedLFM([_assistant(content="It is noon.")])

    model.respond_to_observation(
        "what time is it?",
        ToolObservation(
            request=ToolRequest("time.now", {}),
            result={"iso_time": "2026-09-20T12:00:00+00:00"},
        ),
    )

    assert model.calls[-1]["tools"]


def test_lfm_observation_and_proposal_advertise_the_same_tool_list() -> None:
    """The continuation path must use the same construction as the proposal path."""

    model = QueuedLFM(
        [
            _assistant(
                tool_calls=[{"function": {"name": "time.now", "arguments": {}}}]
            ),
            _assistant(content="It is noon."),
        ]
    )

    action = model.propose("what time is it?")
    assert isinstance(action, ToolRequest)
    model.respond_to_observation(
        "what time is it?",
        ToolObservation(request=action, result={"iso_time": "2026-09-20T12:00:00+00:00"}),
    )

    assert model.calls[0]["tools"] == model.calls[1]["tools"]


def test_lfm_observation_parses_a_native_second_tool_request() -> None:
    """A native tool_call in an observation response becomes the next ToolRequest.

    This is the adapter half of multi-tool continuation: with tools advertised,
    the response can name a *different* tool and the existing parsing path turns
    it into a ToolRequest the loop can execute.
    """

    model = QueuedLFM(
        [
            _assistant(
                tool_calls=[
                    {
                        "function": {
                            "name": "calculator.eval",
                            "arguments": {"expression": "2 + 2"},
                        }
                    }
                ]
            )
        ]
    )

    action = model.respond_to_observation(
        "what time is it, and what is 2 + 2?",
        ToolObservation(
            request=ToolRequest("time.now", {}),
            result={"iso_time": "2026-09-20T12:00:00+00:00"},
        ),
    )

    assert isinstance(action, ToolRequest)
    assert action.name == "calculator.eval"
    assert action.arguments == {"expression": "2 + 2"}


def test_lfm_think_setting_is_omitted_by_default_and_sent_when_explicit(monkeypatch) -> None:
    captured: list[dict] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def __iter__(self):
            yield b'{"message":{"role":"assistant","content":"hello"},"done":false}\n'
            yield b'{"message":{"role":"assistant","content":""},"done":true}\n'

    def fake_urlopen(request, timeout):
        import json

        captured.append(json.loads(request.data.decode("utf-8")))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    LlamaCppLFMModel(endpoint="http://localhost:11434")._chat(
        [{"role": "user", "content": "hi"}]
    )
    LlamaCppLFMModel(endpoint="http://localhost:11434", think=False)._chat(
        [{"role": "user", "content": "hi"}]
    )

    assert "think" not in captured[0]
    assert captured[1]["think"] is False


def test_lfm_repair_fails_after_exactly_one_retry() -> None:
    model = QueuedLFM(
        [
            _assistant(
                tool_calls=[
                    {
                        "function": {
                            "name": "terminal.run",
                            "arguments": [],
                        }
                    }
                ]
            ),
            _tool('{"type":"tool","name":"terminal.run","arguments":[]}'),
        ]
    )

    with pytest.raises(ValueError, match="Model repair failed"):
        model.propose("run pwd")

    assert len(model.calls) == 2


# 8. invalid tool name
def test_lfm_repairs_unknown_tool_once() -> None:
    model = QueuedLFM(
        [
            _tool('{"type":"tool","name":"terminal.execute","arguments":{"command":"pwd"}}'),
            _tool('{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}'),
        ]
    )

    action = model.propose("run pwd")

    assert isinstance(action, ToolRequest)
    assert action.name == "terminal.run"
    assert len(model.calls) == 2


# 9. invalid arguments
def test_lfm_repairs_invalid_argument_type_once() -> None:
    model = QueuedLFM(
        [
            _tool('{"type":"tool","name":"terminal.run","arguments":{"command":123}}'),
            _tool('{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}'),
        ]
    )

    action = model.propose("run pwd")

    assert isinstance(action, ToolRequest)
    assert action.arguments == {"command": "pwd"}
    assert len(model.calls) == 2


# 10. laptop-vs-Jetson tool routing
def test_lfm_laptop_intent_selects_laptop_tool() -> None:
    model = QueuedLFM(
        [_tool('{"type":"tool","name":"laptop.terminal.run","arguments":{"command":"pwd"}}')]
    )

    action = model.propose("on my laptop, run pwd")

    assert isinstance(action, ToolRequest)
    assert action.name == "laptop.terminal.run"


def test_lfm_laptop_intent_repairs_terminal_run_to_laptop() -> None:
    model = QueuedLFM(
        [
            _tool('{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}'),
            _tool('{"type":"tool","name":"laptop.terminal.run","arguments":{"command":"pwd"}}'),
        ]
    )

    action = model.propose("on my laptop, run pwd")

    assert isinstance(action, ToolRequest)
    assert action.name == "laptop.terminal.run"
    assert len(model.calls) == 2


def test_lfm_laptop_intent_fails_if_retry_still_selects_terminal_run() -> None:
    model = QueuedLFM(
        [
            _tool('{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}'),
            _tool('{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}'),
        ]
    )

    with pytest.raises(ValueError, match="Model repair failed"):
        model.propose("on my laptop, run pwd")

    assert len(model.calls) == 2


def test_lfm_observation_falls_back_to_summary_on_blank() -> None:
    model = QueuedLFM([_assistant(content="   ")])
    observation = ToolObservation(
        request=ToolRequest("filesystem.list", {"path": "."}),
        result={"entries": [{"name": "src"}]},
    )

    action = model.respond_to_observation("list files", observation)

    assert isinstance(action, FinalResponse)
    assert action.text == "Directory entries: src"


@pytest.mark.skipif(
    os.environ.get("ODESSY_RUN_LFM_INTEGRATION") != "1",
    reason="requires a running Ollama LFM2.5 backend (set ODESSY_RUN_LFM_INTEGRATION=1)",
)
def test_lfm_live_integration_smoke() -> None:
    model = LlamaCppLFMModel()
    action = model.propose("run pwd")
    assert isinstance(action, (ToolRequest, FinalResponse))
