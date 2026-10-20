"""The module move must preserve import identity and repository resource paths."""

import importlib
from pathlib import Path

import pytest


GROUPS = {
    "runtime": "agent_loop types repeat_guard tool_timeout",
    "models": "cloud_model ollama_model lfm_model fake_model",
    "tools": "calculator mcp_server external_tools node_mcp tool_contracts",
    "authority": "policy workspace_authority command_bridge redaction output_retention spill",
    "config": "config_file deployment doctor",
}


@pytest.mark.parametrize(
    "group,name",
    [(group, name) for group, names in GROUPS.items() for name in names.split()],
)
def test_old_imports_share_the_relocated_module(group: str, name: str) -> None:
    old = importlib.import_module(f"odessy.{name}")
    new = importlib.import_module(f"odessy.{group}.{name}")
    assert old is new


def test_repository_resources_survive_the_move() -> None:
    from odessy.config.config_file import default_config_file
    from odessy.tools.node_mcp import _public_repo_root

    root = Path(__file__).resolve().parents[1]
    assert default_config_file() == root / "config" / "odessy.env"
    assert _public_repo_root() == root
