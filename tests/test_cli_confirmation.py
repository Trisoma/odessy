from pathlib import Path

from odessy.cli import _build_agent, _confirm_tool_call
from odessy.models.fake_model import FakeModel
from odessy.runtime.types import PolicyDecision, ToolRequest


def test_cli_confirmation_accepts_explicit_yes(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")

    accepted = _confirm_tool_call(
        PolicyDecision("confirm", "filesystem.write modifies the workspace", "medium"),
        ToolRequest(name="filesystem.write", arguments={"path": "notes.txt", "content": "hello"}),
    )

    assert accepted is True


def test_cli_confirmation_rejects_default_empty_response(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    accepted = _confirm_tool_call(
        PolicyDecision("confirm", "filesystem.write modifies the workspace", "medium"),
        ToolRequest(name="filesystem.write", arguments={"path": "notes.txt", "content": "hello"}),
    )

    assert accepted is False


def test_cli_build_agent_wires_human_confirmation(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    agent = _build_agent(tmp_path, FakeModel())

    accepted = agent._confirm(
        PolicyDecision("confirm", "filesystem.write modifies the workspace", "medium"),
        ToolRequest(name="filesystem.write", arguments={"path": "notes.txt", "content": "hello"}),
    )

    assert accepted is True
