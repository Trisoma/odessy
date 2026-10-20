from __future__ import annotations

import pytest

from odessy.models.cloud_model import cloud_tools_openai, system_prompt as cloud_system_prompt
from odessy.config.deployment import (
    DeploymentConfigError,
    command_tool_for_laptop,
    deployment_mode,
    environment_prompt,
    local_os,
    model_tool_names,
    remote_tool_server_urls,
)
from odessy.models.ollama_model import (
    _system_prompt,
    _text_system_prompt,
    _validate_tool_selection_for_user_intent,
)
from odessy.tools.tool_contracts import (
    TOOL_REQUEST_SCHEMAS,
    build_openai_tools,
    render_tool_contracts,
    tool_description,
)
from odessy.runtime.types import ToolRequest


@pytest.fixture
def single(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODESSY_DEPLOYMENT", "single")
    monkeypatch.setenv("ODESSY_LOCAL_OS", "windows")


# --- mode detection ---------------------------------------------------------------


def test_unset_mode_is_single_without_a_remote_server() -> None:
    assert deployment_mode({}) == "single"


def test_unset_mode_is_split_when_a_tool_server_url_is_configured() -> None:
    # An existing two-machine setup keeps working with no new setting.
    assert deployment_mode({"ODESSY_TOOL_SERVER_URL": "http://10.1.1.1:8765/mcp"}) == "split"
    assert deployment_mode({"ODESSY_MCP_SERVER_URLS": "http://a/mcp"}) == "split"


def test_a_blank_url_does_not_select_split() -> None:
    assert deployment_mode({"ODESSY_TOOL_SERVER_URL": "  "}) == "single"


def test_explicit_mode_wins_over_detection() -> None:
    env = {"ODESSY_DEPLOYMENT": " Single ", "ODESSY_TOOL_SERVER_URL": "http://10.1.1.1:8765/mcp"}
    assert deployment_mode(env) == "single"
    assert deployment_mode({"ODESSY_DEPLOYMENT": "split"}) == "split"


def test_unknown_mode_is_an_error_not_a_guess() -> None:
    with pytest.raises(DeploymentConfigError, match="single, split"):
        deployment_mode({"ODESSY_DEPLOYMENT": "both"})


def test_remote_urls_accept_commas_and_semicolons_and_prefer_the_plural_variable() -> None:
    env = {
        "ODESSY_MCP_SERVER_URLS": " http://a/mcp ; http://b/mcp,,",
        "ODESSY_TOOL_SERVER_URL": "http://ignored/mcp",
    }
    assert remote_tool_server_urls(env) == ["http://a/mcp", "http://b/mcp"]
    assert remote_tool_server_urls({}) == []


# --- tools offered ----------------------------------------------------------------


def test_single_mode_never_offers_the_laptop_tool() -> None:
    names = model_tool_names("single")
    assert "laptop.terminal.run" not in names
    assert "terminal.run" in names
    assert model_tool_names("split") == list(TOOL_REQUEST_SCHEMAS)


def test_laptop_requests_use_the_one_command_tool_in_single_mode() -> None:
    assert command_tool_for_laptop("single") == "terminal.run"
    assert command_tool_for_laptop("split") == "laptop.terminal.run"


def test_rendered_contracts_follow_the_mode(single: None) -> None:
    assert "laptop.terminal.run" not in {tool["function"]["name"] for tool in build_openai_tools()}
    assert "laptop.terminal.run" not in render_tool_contracts()
    assert tool_description("terminal.run").startswith("Run a shell command on the user's computer")
    # An explicit name list is honoured as given.
    assert build_openai_tools(["laptop.terminal.run"])[0]["function"]["name"] == "laptop.terminal.run"


def test_split_mode_contracts_are_unchanged() -> None:
    # conftest pins split.
    assert {tool["function"]["name"] for tool in build_openai_tools()} == set(TOOL_REQUEST_SCHEMAS)
    assert tool_description("terminal.run").startswith("Run a shell command in the local workspace")


def test_cloud_models_are_offered_the_single_mode_tools(single: None) -> None:
    names = {tool["function"]["name"] for tool in cloud_tools_openai()}
    assert "terminal__run" in names
    assert "laptop__terminal__run" not in names


# --- operating system and the environment sentence --------------------------------


def test_local_os_override_accepts_aliases_and_rejects_unknown_values() -> None:
    assert local_os({"ODESSY_LOCAL_OS": "Darwin"}) == "macos"
    assert local_os({"ODESSY_LOCAL_OS": "win32"}) == "windows"
    with pytest.raises(DeploymentConfigError):
        local_os({"ODESSY_LOCAL_OS": "beos"})
    assert local_os({}) in {"windows", "linux", "macos"}


def test_single_mode_tells_the_model_the_shell() -> None:
    text = environment_prompt(env={"ODESSY_DEPLOYMENT": "single", "ODESSY_LOCAL_OS": "windows"})
    assert "terminal.run" in text
    assert "Windows PowerShell" in text
    assert "laptop.terminal.run" not in text

    text = environment_prompt(env={"ODESSY_DEPLOYMENT": "single", "ODESSY_LOCAL_OS": "linux"})
    assert "Linux; commands run in sh" in text


def test_split_mode_names_both_machines_and_the_laptop_os_when_known() -> None:
    env = {"ODESSY_DEPLOYMENT": "split", "ODESSY_LOCAL_OS": "linux", "ODESSY_LAPTOP_OS": "windows"}
    text = environment_prompt(env=env)
    assert "terminal.run runs commands on this machine (Linux; commands run in sh." in text
    assert "df -h" in text and "Get-PSDrive" in text
    assert "laptop.terminal.run runs commands on the user's laptop" in text
    assert "Windows PowerShell" in text

    unknown = environment_prompt(env={"ODESSY_DEPLOYMENT": "split", "ODESSY_LOCAL_OS": "linux"})
    assert unknown.endswith("a separate machine.")


def test_environment_sentence_names_only_offered_tools() -> None:
    env = {"ODESSY_DEPLOYMENT": "split", "ODESSY_LOCAL_OS": "linux"}
    assert "laptop.terminal.run" not in environment_prompt(["terminal.run"], env)
    assert "this machine" not in environment_prompt(["laptop.terminal.run"], env)
    assert environment_prompt(["git.status"], env) == ""
    assert environment_prompt(["git.status"], {"ODESSY_DEPLOYMENT": "single"}) == ""


def test_local_prompts_carry_the_environment(single: None) -> None:
    assert "Windows PowerShell" in _system_prompt()
    assert "Windows PowerShell" in _text_system_prompt("run dir")
    assert "\n\n" not in _system_prompt()


def test_cloud_prompt_uses_encoded_names_for_the_mode(single: None, monkeypatch: pytest.MonkeyPatch) -> None:
    prompt = cloud_system_prompt()
    assert "terminal__run" in prompt
    assert "laptop__terminal__run" not in prompt
    assert "terminal.run" not in prompt

    monkeypatch.setenv("ODESSY_DEPLOYMENT", "split")
    prompt = cloud_system_prompt()
    assert "must use laptop__terminal__run" in prompt
    assert "terminal__run runs commands on this machine" in prompt


# --- laptop wording in single mode --------------------------------------------------


def test_laptop_wording_accepts_terminal_run_and_rejects_others_in_single_mode(single: None) -> None:
    _validate_tool_selection_for_user_intent(
        "on my laptop, run dir", ToolRequest(name="terminal.run", arguments={"command": "dir"})
    )
    with pytest.raises(ValueError, match="must use terminal.run"):
        _validate_tool_selection_for_user_intent(
            "on my laptop, run dir", ToolRequest(name="time.now", arguments={})
        )


def test_laptop_wording_requires_the_laptop_tool_in_split_mode() -> None:
    with pytest.raises(ValueError, match="must use laptop.terminal.run"):
        _validate_tool_selection_for_user_intent(
            "on my laptop, run dir", ToolRequest(name="terminal.run", arguments={"command": "dir"})
        )
