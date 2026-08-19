from __future__ import annotations

import pytest

from odessy.ollama_model import _parse_model_action, _proposal_prompt
from odessy.types import FinalResponse, ToolRequest


def test_proposal_prompt_includes_terminal_run_argument_schema() -> None:
    prompt = _proposal_prompt("run pwd")

    assert '"name": "terminal.run"' in prompt
    assert '"command": "string"' in prompt
    assert '"cwd": "string, optional"' in prompt
    assert '"timeout": "integer, optional"' in prompt


def test_proposal_prompt_includes_laptop_terminal_run_argument_schema() -> None:
    prompt = _proposal_prompt("run pwd on my laptop")

    assert '"name": "laptop.terminal.run"' in prompt
    assert '"command": "string"' in prompt
    assert '"cwd": "string, optional"' in prompt
    assert '"timeout": "integer, optional"' in prompt


def test_proposal_prompt_shows_empty_arguments_for_git_status() -> None:
    prompt = _proposal_prompt("git status")

    assert '"name": "git.status"' in prompt
    assert '"arguments": {}' in prompt


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
