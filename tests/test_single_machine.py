from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client

from shell_model import ShellModel

import odessy.cli as cli
import odessy.config.config_file as config_file
import odessy.tools.node_mcp as node_mcp
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.models.cloud_model import cloud_tools_openai
from odessy.config.deployment import model_tool_names
from odessy.tools.mcp_server import build_odessy_mcp_server
from odessy.models.ollama_model import (
    _model_output_error,
    _text_system_prompt,
)
from odessy.authority.policy import OdessyPolicy
from odessy.tools.tool_contracts import build_openai_tools


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _tool_names(server: Any) -> set[str]:
    async with Client(server) as client:
        tools = await client.list_tools()
    return {tool.name for tool in getattr(tools, "tools", tools)}


# --- Python tool server -----------------------------------------------------------


@pytest.mark.anyio
async def test_python_server_can_leave_terminal_run_to_another_server(tmp_path: Path) -> None:
    assert "terminal.run" in await _tool_names(build_odessy_mcp_server(workspace_root=tmp_path))
    names = await _tool_names(build_odessy_mcp_server(workspace_root=tmp_path, include_terminal=False))
    assert names == {"time.now", "calculator.eval"}


# --- which servers the CLI starts -------------------------------------------------


class _FakeNode:
    instances: list["_FakeNode"] = []

    def __init__(self, workspace_root: Path, *, host: str, extra_env: dict[str, str]) -> None:
        self.workspace_root = workspace_root
        self.host = host
        self.extra_env = extra_env
        self.entered = False
        self.exited = False
        _FakeNode.instances.append(self)

    async def __aenter__(self) -> str:
        self.entered = True
        return "http://127.0.0.1:50123/mcp"

    async def __aexit__(self, *exc: object) -> None:
        self.exited = True


def _servers_of(servers: tuple[object, ...]) -> list[object]:
    assert all(isinstance(item, ExecutionTarget) for item in servers)
    return [item.server for item in servers]  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_split_mode_uses_the_local_server_and_the_configured_laptop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODESSY_TOOL_SERVER_URL", "http://10.1.1.1:8765/mcp")
    monkeypatch.setattr(cli, "NodeToolServerHttp", _FakeNode)
    _FakeNode.instances = []

    async with cli._tool_servers(tmp_path) as servers:
        local, laptop = _servers_of(servers)

    assert laptop == "http://10.1.1.1:8765/mcp"
    assert "terminal.run" in await _tool_names(local)
    assert _FakeNode.instances == []


@pytest.mark.anyio
async def test_single_mode_starts_node_on_loopback_and_hands_it_terminal_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODESSY_DEPLOYMENT", "single")
    monkeypatch.setattr(cli, "node_tool_server_dependencies_installed", lambda: True)
    monkeypatch.setattr(cli, "NodeToolServerHttp", _FakeNode)
    _FakeNode.instances = []

    async with cli._tool_servers(tmp_path) as servers:
        local, node_url = _servers_of(servers)
        (node,) = _FakeNode.instances
        assert node.entered and not node.exited

    assert node.exited
    assert node.host == "127.0.0.1"
    assert node.extra_env == {"ODESSY_DEPLOYMENT": "single"}
    assert node_url == "http://127.0.0.1:50123/mcp"
    assert await _tool_names(local) == {"time.now", "calculator.eval"}


@pytest.mark.anyio
async def test_single_mode_stops_node_when_the_turn_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODESSY_DEPLOYMENT", "single")
    monkeypatch.setattr(cli, "node_tool_server_dependencies_installed", lambda: True)
    monkeypatch.setattr(cli, "NodeToolServerHttp", _FakeNode)
    _FakeNode.instances = []

    with pytest.raises(PermissionError):
        async with cli._tool_servers(tmp_path):
            raise PermissionError("denied")

    assert _FakeNode.instances[0].exited


@pytest.mark.anyio
async def test_single_mode_without_node_runs_degraded_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ODESSY_DEPLOYMENT", "single")
    monkeypatch.setattr(cli, "node_tool_server_dependencies_installed", lambda: False)

    async with cli._tool_servers(tmp_path) as servers:
        (local,) = _servers_of(servers)
        mounted = await _tool_names(local)
        assert set(model_tool_names()) == mounted
        assert {tool["function"]["name"] for tool in build_openai_tools()} == mounted
        assert {tool["function"]["name"] for tool in cloud_tools_openai()} == {
            name.replace(".", "__") for name in mounted
        }
        assert "filesystem.list" not in _text_system_prompt("list files")
        error = _model_output_error(
            '{"name":"filesystem.list","arguments":{}}', ValueError("unavailable"), "list files"
        )
        assert "filesystem.list" not in error.tool_contract

    assert len(model_tool_names()) == 13  # Availability does not leak into later runs.
    assert "terminal.run" in await _tool_names(local)
    warning = capsys.readouterr().err
    assert "Node tool server is not installed" in warning
    assert "python -m odessy doctor" in warning


@pytest.mark.anyio
async def test_single_mode_node_startup_failure_points_at_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Broken(_FakeNode):
        async def __aenter__(self) -> str:
            raise RuntimeError("Node MCP tool server exited before accepting connections")

    monkeypatch.setenv("ODESSY_DEPLOYMENT", "single")
    monkeypatch.setattr(cli, "node_tool_server_dependencies_installed", lambda: True)
    monkeypatch.setattr(cli, "NodeToolServerHttp", _Broken)

    with pytest.raises(SystemExit, match="doctor"):
        async with cli._tool_servers(tmp_path):
            pass


@pytest.mark.anyio
async def test_invalid_deployment_is_reported_as_a_clean_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODESSY_DEPLOYMENT", "cluster")
    with pytest.raises(SystemExit, match="ODESSY_DEPLOYMENT"):
        async with cli._tool_servers(tmp_path):
            pass


# --- Node process lifecycle -------------------------------------------------------


@pytest.mark.anyio
async def test_node_startup_failure_reports_the_server_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A stand-in "server" that fails the way a broken install does: it writes the
    # reason to stderr and exits. Python plays node, so this runs on any machine.
    entry = tmp_path / "server.py"
    entry.write_text(
        "import sys\nsys.stderr.write('Error: Cannot find module @modelcontextprotocol/sdk')\nsys.exit(3)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(node_mcp, "node_tool_server_dependencies_installed", lambda: True)
    monkeypatch.setattr(node_mcp.shutil, "which", lambda name: sys.executable)
    monkeypatch.setattr(node_mcp, "_build_is_current", lambda tools_dir: True)
    monkeypatch.setattr(node_mcp, "_built_server_entry", lambda tools_dir: entry)
    popen = node_mcp.subprocess.Popen
    launch_options = []

    def record_launch(*args, **kwargs):
        launch_options.append(kwargs)
        return popen(*args, **kwargs)

    monkeypatch.setattr(node_mcp.subprocess, "Popen", record_launch)

    server = node_mcp.NodeToolServerHttp(tmp_path, extra_env={"ODESSY_DEPLOYMENT": "single"})
    with pytest.raises(RuntimeError, match="Cannot find module"):
        await server.__aenter__()
    assert server._process is None
    assert launch_options[0]["creationflags"] == getattr(
        node_mcp.subprocess, "CREATE_NO_WINDOW", 0
    )


# --- config files -----------------------------------------------------------------


def test_explicit_config_file_replaces_both_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ODESSY_CONFIG_FILE", str(tmp_path / "only.env"))
    assert config_file.config_files() == [tmp_path / "only.env"]


def test_user_config_overrides_the_committed_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    user = tmp_path / "user.env"
    repo = tmp_path / "repo.env"
    user.write_text("ODESSY_TEST_VALUE=user\n", encoding="utf-8")
    repo.write_text("ODESSY_TEST_VALUE=repo\nODESSY_TEST_ONLY_REPO=repo\n", encoding="utf-8")
    monkeypatch.delenv("ODESSY_CONFIG_FILE")
    monkeypatch.delenv("ODESSY_TEST_VALUE", raising=False)
    monkeypatch.delenv("ODESSY_TEST_ONLY_REPO", raising=False)
    monkeypatch.setattr(config_file, "user_config_file", lambda: user)
    monkeypatch.setattr(config_file, "default_config_file", lambda: repo)

    assert config_file.config_files() == [user, repo]
    config_file.load_config_env()

    assert os.environ["ODESSY_TEST_VALUE"] == "user"
    assert os.environ["ODESSY_TEST_ONLY_REPO"] == "repo"


def test_committed_config_selects_no_deployment_by_itself() -> None:
    values = config_file.read_env_file(config_file.default_config_file())
    assert "ODESSY_TOOL_SERVER_URL" not in values
    assert "ODESSY_MCP_SERVER_URLS" not in values
    assert "ODESSY_DEPLOYMENT" not in values


# --- the real Node server, end to end ---------------------------------------------


@pytest.mark.anyio
async def test_single_mode_runs_a_command_through_the_local_node_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not node_mcp.node_tool_server_dependencies_installed():
        pytest.skip("Node tool server dependencies are not installed")
    monkeypatch.setenv("ODESSY_DEPLOYMENT", "single")
    (tmp_path / "README.md").write_bytes(b"single machine\n")

    async with cli._tool_servers(tmp_path) as servers:
        local, node_url = _servers_of(servers)
        node_tools = await _tool_names(node_url)
        assert set(model_tool_names()) == node_tools | await _tool_names(local)
        assert len(model_tool_names()) == 13
        agent = OdessyAgent(
            model=ShellModel(),
            policy=OdessyPolicy(workspace_root=tmp_path),
            mcp_server=servers,
            confirm=True,
        )
        command = await agent.run_turn("run echo odessy-single")
        read = await agent.run_turn("read README.md")

    assert "terminal.run" in node_tools
    assert "laptop.terminal.run" not in node_tools
    assert {"filesystem.read", "git.status"} <= node_tools

    assert command.tool_request is not None and command.tool_request.name == "terminal.run"
    assert command.tool_result is not None
    assert command.tool_result["exit_code"] == 0, command.tool_result
    assert "odessy-single" in command.tool_result["stdout"]
    # Only the Node server reports spill fields: proof the command ran there.
    assert "stdout_spill" in command.tool_result

    assert read.tool_result is not None and read.tool_result["content"] == "single machine\n"


# --- console output ---------------------------------------------------------------


def test_transport_request_logs_are_quiet_unless_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    import logging

    for name in ("httpx", "httpx2", "mcp.client"):
        monkeypatch.setattr(logging.getLogger(name), "level", logging.NOTSET)

    monkeypatch.setenv("ODESSY_DEBUG_HTTP", "1")
    cli._quiet_transport_logs()
    assert logging.getLogger("httpx2").level == logging.NOTSET

    monkeypatch.delenv("ODESSY_DEBUG_HTTP")
    cli._quiet_transport_logs()
    assert all(
        logging.getLogger(name).level == logging.WARNING for name in ("httpx", "httpx2", "mcp.client")
    )
