from __future__ import annotations

import os

import pytest

from odessy.cli import _build_model
from odessy.lfm_model import LlamaCppLFMModel, normalize_ollama_chat_response
from odessy.ollama_model import _OllamaToolsUnsupported
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
    assert model.calls[0]["tools"]
    assert model.calls[1]["tools"] is None


def test_lfm_normalize_ollama_response() -> None:
    normalized = normalize_ollama_chat_response(
        {
            "message": {
                "role": "assistant",
                "content": "hi",
                "tool_calls": [],
            }
        }
    )

    assert normalized["message"]["content"] == "hi"
    assert normalized["message"]["tool_calls"] == []


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
