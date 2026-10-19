from __future__ import annotations

import pytest

from odessy.agent_loop import OdessyAgent
from odessy.ollama_model import (
    OllamaModel,
    _parse_model_action,
    _system_prompt,
)
from odessy.policy import OdessyPolicy
from odessy.types import FinalResponse, ToolObservation, ToolRequest


class QueuedOllamaModel(OllamaModel):
    def __init__(self, outputs: list[str]) -> None:
        object.__setattr__(self, "outputs", list(outputs))
        object.__setattr__(self, "prompts", [])

    def _chat(self, messages, tools=None):
        system = next(
            (m.get("content") for m in messages if m.get("role") == "system"),
            messages[0].get("content") if messages else "",
        )
        self.prompts.append(system)
        if not self.outputs:
            raise AssertionError("No queued Ollama output available")
        return {"message": {"content": self.outputs.pop(0)}}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_system_prompt_never_preselects_a_tool() -> None:
    # Keyword routing is gone: the model always chooses among every offered tool.
    prompt = _system_prompt()

    assert "tool-calling adapter" in prompt
    assert "maps to" not in prompt
    assert "more tools after its result" in prompt


def test_valid_tool_json_becomes_tool_request() -> None:
    action = _parse_model_action(
        '{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}'
    )

    assert isinstance(action, ToolRequest)
    assert action.name == "terminal.run"
    assert action.arguments == {"command": "pwd"}


def test_valid_laptop_terminal_tool_json_becomes_tool_request() -> None:
    action = _parse_model_action(
        '{"type":"tool","name":"laptop.terminal.run","arguments":{"command":"pwd"}}'
    )

    assert isinstance(action, ToolRequest)
    assert action.name == "laptop.terminal.run"
    assert action.arguments == {"command": "pwd"}


def test_tool_json_with_list_arguments_is_rejected() -> None:
    with pytest.raises(ValueError, match="terminal.run"):
        _parse_model_action('{"type":"tool","name":"terminal.run","arguments":[]}')


def test_tool_json_missing_required_terminal_run_command_is_rejected() -> None:
    with pytest.raises(ValueError, match="terminal.run"):
        _parse_model_action('{"type":"tool","name":"terminal.run","arguments":{"cwd":"."}}')


def test_final_response_json_remains_final_response() -> None:
    action = _parse_model_action('{"type":"final","text":"done"}')

    assert isinstance(action, FinalResponse)
    assert action.text == "done"


def test_ollama_valid_first_tool_request_does_not_retry() -> None:
    model = QueuedOllamaModel(
        ['{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}']
    )

    action = model.propose("run pwd")

    assert isinstance(action, ToolRequest)
    assert action.name == "terminal.run"
    assert len(model.prompts) == 1


def test_ollama_debug_output_shows_valid_first_action(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ODESSY_DEBUG_MODEL_REPAIR", "1")
    model = QueuedOllamaModel(
        ['{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}']
    )

    action = model.propose("run pwd")

    assert isinstance(action, ToolRequest)
    captured = capsys.readouterr()
    assert "parsed tool request" in captured.out
    assert "terminal.run" in captured.out
    assert '"command": "pwd"' in captured.out


def test_ollama_repairs_malformed_json_once() -> None:
    model = QueuedOllamaModel(
        [
            '{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"',
            '{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}',
        ]
    )

    action = model.propose("run pwd")

    assert isinstance(action, ToolRequest)
    assert action.arguments == {"command": "pwd"}
    assert len(model.prompts) == 2
    assert "Your previous JSON output was invalid." in model.prompts[1]
    assert "Validation error:" in model.prompts[1]


def test_ollama_repairs_list_arguments_once() -> None:
    model = QueuedOllamaModel(
        [
            '{"type":"tool","name":"terminal.run","arguments":[]}',
            '{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}',
        ]
    )

    action = model.propose("run pwd")

    assert isinstance(action, ToolRequest)
    assert action.arguments == {"command": "pwd"}
    assert len(model.prompts) == 2


def test_ollama_repairs_invalid_argument_type_once() -> None:
    model = QueuedOllamaModel(
        [
            '{"type":"tool","name":"terminal.run","arguments":{"command":123}}',
            '{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}',
        ]
    )

    action = model.propose("run pwd")

    assert isinstance(action, ToolRequest)
    assert action.arguments == {"command": "pwd"}
    assert len(model.prompts) == 2


def test_ollama_repairs_unknown_tool_once() -> None:
    model = QueuedOllamaModel(
        [
            '{"type":"tool","name":"terminal.execute","arguments":{"command":"pwd"}}',
            '{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}',
        ]
    )

    action = model.propose("run pwd")

    assert isinstance(action, ToolRequest)
    assert action.name == "terminal.run"
    assert len(model.prompts) == 2
    assert "Unknown tool requested by model: terminal.execute" in model.prompts[1]


def test_ollama_repairs_laptop_intent_that_selected_terminal_run_once() -> None:
    model = QueuedOllamaModel(
        [
            '{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}',
            '{"type":"tool","name":"laptop.terminal.run","arguments":{"command":"pwd"}}',
        ]
    )

    action = model.propose("on my laptop, run pwd")

    assert isinstance(action, ToolRequest)
    assert action.name == "laptop.terminal.run"
    assert action.arguments == {"command": "pwd"}
    assert len(model.prompts) == 2
    assert "Laptop command requests must use laptop.terminal.run" in model.prompts[1]
    assert '"name": "laptop.terminal.run"' in model.prompts[1]


def test_ollama_laptop_intent_fails_if_retry_still_selects_terminal_run() -> None:
    model = QueuedOllamaModel(
        [
            '{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}',
            '{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}',
        ]
    )

    with pytest.raises(ValueError, match="Model repair failed"):
        model.propose("on my laptop, run pwd")

    assert len(model.prompts) == 2


def test_ollama_laptop_intent_fails_if_retry_selects_other_valid_tool() -> None:
    model = QueuedOllamaModel(
        [
            '{"type":"tool","name":"laptop.terminal.run","arguments":{"command":"pwd","args":{"--interactive"}}}',
            '{"type":"tool","name":"time.now","arguments":{}}',
        ]
    )

    with pytest.raises(ValueError, match="Laptop command requests must use laptop.terminal.run"):
        model.propose("on my laptop, run pwd")

    assert len(model.prompts) == 2


def test_ollama_repair_prompt_recovers_tool_name_from_malformed_laptop_request() -> None:
    model = QueuedOllamaModel(
        [
            '{"type":"tool","name":"laptop.terminal.run","arguments":{"command":"pwd","args":{"--interactive"}}}',
            '{"type":"tool","name":"laptop.terminal.run","arguments":{"command":"pwd"}}',
        ]
    )

    action = model.propose("on my laptop, run pwd")

    assert isinstance(action, ToolRequest)
    assert action.name == "laptop.terminal.run"
    # Without a routed tool to focus on, the repair prompt carries every contract.
    assert "laptop.terminal.run:" in model.prompts[1]


def test_ollama_invalid_retry_fails_after_exactly_one_repair_attempt() -> None:
    model = QueuedOllamaModel(
        [
            "not json",
            '{"type":"tool","name":"terminal.run","arguments":[]}',
        ]
    )

    with pytest.raises(ValueError, match="Model repair failed"):
        model.propose("run pwd")

    assert len(model.prompts) == 2


def test_ollama_valid_final_response_does_not_retry() -> None:
    model = QueuedOllamaModel(['{"type":"final","text":"done"}'])

    action = model.propose("say hi")

    assert isinstance(action, FinalResponse)
    assert action.text == "done"
    assert len(model.prompts) == 1


def test_ollama_blank_observation_response_falls_back_to_summary() -> None:
    model = QueuedOllamaModel(["   "])
    observation = ToolObservation(
        request=ToolRequest("filesystem.list", {"path": "."}),
        result={"entries": [{"name": "src"}, {"name": "tests"}]},
    )

    action = model.respond_to_observation("list files", observation)

    assert isinstance(action, FinalResponse)
    assert action.text == "Directory entries: src, tests"


def test_ollama_generic_observation_response_falls_back_to_summary() -> None:
    model = QueuedOllamaModel(["Yes."])
    observation = ToolObservation(
        request=ToolRequest("filesystem.list", {"path": "."}),
        result={"entries": [{"name": "src"}, {"name": "tests"}]},
    )

    action = model.respond_to_observation("list files", observation)

    assert isinstance(action, FinalResponse)
    assert action.text == "Directory entries: src, tests"


def test_ollama_repair_debug_output_is_optional(monkeypatch, capsys) -> None:
    model = QueuedOllamaModel(
        [
            '{"type":"tool","name":"terminal.run","arguments":[]}',
            '{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}',
        ]
    )

    model.propose("run pwd")
    quiet = capsys.readouterr()
    assert quiet.out == ""

    monkeypatch.setenv("ODESSY_DEBUG_MODEL_REPAIR", "1")
    model = QueuedOllamaModel(
        [
            '{"type":"tool","name":"terminal.run","arguments":[]}',
            '{"type":"tool","name":"terminal.run","arguments":{"command":"pwd"}}',
        ]
    )

    model.propose("run pwd")
    noisy = capsys.readouterr()

    assert "first Gemma proposal" in noisy.out
    assert "validation failure" in noisy.out
    assert "second Gemma proposal" in noisy.out


@pytest.mark.anyio
async def test_valid_tool_request_denied_by_policy_does_not_trigger_repair(tmp_path) -> None:
    model = QueuedOllamaModel(
        ['{"type":"tool","name":"filesystem.read","arguments":{"path":".env"}}']
    )
    agent = OdessyAgent(
        model=model,
        policy=OdessyPolicy(workspace_root=tmp_path),
        mcp_server=object(),
    )

    with pytest.raises(PermissionError, match="credentials or secrets"):
        await agent.run_turn("read .env")

    assert len(model.prompts) == 1
