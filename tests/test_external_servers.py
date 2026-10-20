"""Third-party MCP servers: config, discovery, contracts, policy, and the agent loop.

No real third-party server is contacted. An in-process MCP server stands in for one,
reached through ``NamespacedServer`` exactly as a configured server would be.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

import odessy.cli as cli
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.models.cloud_model import cloud_tools_openai, decode_tool_name
from odessy.tools.external_tools import (
    ExternalTool,
    NamespacedServer,
    ServerConfig,
    ServerConfigError,
    discover_tools,
    external_tool,
    external_tool_scope,
    load_server_configs,
    tool_from_mcp,
)
from odessy.authority.policy import OdessyPolicy
from odessy.tools.tool_contracts import build_openai_tools, tool_description, validate_tool_arguments
from odessy.runtime.types import FinalResponse, ToolRequest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "servers.toml"
    path.write_text(text, encoding="utf-8")
    return path


# --- config -----------------------------------------------------------------------


def test_no_servers_file_means_no_servers(tmp_path: Path) -> None:
    assert load_server_configs(tmp_path / "missing.toml") == []


def test_servers_file_location_comes_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, '[servers.notes]\ntransport = "http"\nurl = "http://127.0.0.1:9000/mcp"\n')
    monkeypatch.setenv("ODESSY_SERVERS_FILE", str(path))
    (server,) = load_server_configs()
    assert server.name == "notes"


def test_a_full_config_parses(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
[servers.notes]
transport = "stdio"
command = ["notes-mcp", "--root", "notes"]
env = ["NOTES_DIR"]
trust = "trusted"
workspace_paths = ["path"]
timeout = 10

[servers.notes.tools.search]
policy = "allow"

[servers.notes.tools.delete_note]
policy = "deny"

[servers.tracker]
transport = "http"
url = "http://127.0.0.1:9000/mcp"
""",
    )
    notes, tracker = load_server_configs(path)
    assert notes.command == ("notes-mcp", "--root", "notes")
    assert notes.trust == "trusted"
    assert dict(notes.tool_policies) == {"search": "allow", "delete_note": "deny"}
    assert notes.workspace_paths == ("path",)
    assert notes.timeout == 10
    assert tracker.trust == "untrusted"  # the default


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[servers.git]\ntransport = "http"\nurl = "http://x/mcp"\n', "reserved"),
        ('[servers.Notes]\ntransport = "http"\nurl = "http://x/mcp"\n', "lowercase"),
        ('[servers.notes]\ntransport = "ftp"\n', "transport"),
        ('[servers.notes]\ntransport = "http"\n', "needs url"),
        ('[servers.notes]\ntransport = "stdio"\n', "needs command"),
        ('[servers.notes]\ntransport = "http"\nurl = "http://x/mcp"\ntrust = "yes"\n', "trust"),
        ('[servers.notes]\ntransport = "http"\nurl = "http://x/mcp"\ncolour = "red"\n', "unknown keys"),
        ('[servers.notes]\ntransport = "http"\nurl = "http://x/mcp"\n[servers.notes.tools.a]\npolicy = "maybe"\n', "policy"),
        ('[servers.notes]\ntransport = "http"\nurl = "http://x/mcp"\ntimeout = 0\n', "timeout"),
        ('[servers.notes]\ntransport = "stdio"\ncommand = ["x"]\nenv = ["NOT A NAME"]\n', "env"),
    ],
)
def test_malformed_configs_fail_loudly(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ServerConfigError, match=message):
        load_server_configs(_write(tmp_path, text))


def test_stdio_servers_get_only_the_base_and_listed_environment() -> None:
    config = ServerConfig(name="notes", transport="stdio", command=("notes-mcp", "-v"), env=("NOTES_DIR",))
    params = config.connection({"PATH": "/bin", "NOTES_DIR": "/n", "OTHER_SECRET": "x", "Path": "C:/w"})
    assert params.command == "notes-mcp"
    assert params.args == ["-v"]
    assert params.env == {"PATH": "/bin", "NOTES_DIR": "/n", "Path": "C:/w"}


# --- discovery --------------------------------------------------------------------


def _tool(name: str, schema: dict[str, Any] | None = None, **hints: bool) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=f"The {name} tool.",
        input_schema=schema if schema is not None else {"type": "object", "properties": {"q": {"type": "string"}}},
        annotations=ToolAnnotations(**hints) if hints else None,
    )


NOTES = ServerConfig(name="notes", transport="http", url="http://127.0.0.1:9000/mcp")


@pytest.mark.parametrize(
    ("tool", "reason"),
    [
        (_tool("bad__name"), "unsupported name"),
        (_tool("bad name"), "unsupported name"),
        (_tool("x", {"type": "array"}), "not an object"),
        (_tool("x", {"type": "object", "properties": {"a": {"type": "nope"}}}), "invalid input schema"),
        (_tool("x", {"type": "object", "description": "y" * 9000}), "too large"),
    ],
)
def test_unusable_tools_are_left_out_with_a_reason(tool: Any, reason: str) -> None:
    result, problem = tool_from_mcp(NOTES, tool)
    assert result is None
    assert reason in (problem or "")


def test_annotations_and_long_descriptions_are_read_carefully() -> None:
    tool = _tool("search", read_only_hint=True, destructive_hint=False)
    tool.description = "word " * 400
    result, problem = tool_from_mcp(NOTES, tool)
    assert problem is None and result is not None
    assert result.public_name == "notes.search"
    assert result.read_only is True and result.destructive is False and result.open_world is None
    assert len(result.description) <= 603


@pytest.mark.anyio
async def test_an_unreachable_server_is_reported_and_the_others_still_load() -> None:
    broken = ServerConfig(name="broken", transport="http", url="http://127.0.0.1:1/mcp")

    async def lister(server: ServerConfig) -> list[Any]:
        if server.name == "broken":
            raise ConnectionRefusedError("refused")
        return [_tool("search"), _tool("search"), _tool("add")]

    tools, reachable, problems = await discover_tools([broken, NOTES], limit=10, list_tools=lister)
    assert [tool.public_name for tool in tools] == ["notes.search", "notes.add"]
    assert reachable == [NOTES]
    assert any("broken: not available" in problem for problem in problems)
    assert any("listed twice" in problem for problem in problems)


@pytest.mark.anyio
async def test_the_external_tool_limit_holds() -> None:
    async def lister(server: ServerConfig) -> list[Any]:
        return [_tool(f"t{i}") for i in range(5)]

    tools, _, problems = await discover_tools([NOTES], limit=3, list_tools=lister)
    assert len(tools) == 3
    assert sum("more than 3" in problem for problem in problems) == 2


# --- contracts --------------------------------------------------------------------


def _external(**overrides: Any) -> ExternalTool:
    base = dict(
        public_name="notes.search",
        remote_name="search",
        server=NOTES,
        description="Search the notes.",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"], "additionalProperties": False},
    )
    base.update(overrides)
    return ExternalTool(**base)


def test_external_tools_are_offered_only_inside_their_scope() -> None:
    assert "notes.search" not in {t["function"]["name"] for t in build_openai_tools()}
    with external_tool_scope([_external()]):
        offered = {t["function"]["name"]: t for t in build_openai_tools()}
        assert "notes.search" in offered
        assert offered["notes.search"]["function"]["parameters"]["required"] == ["q"]
        assert tool_description("notes.search").startswith("[Third-party tool from the notes server]")
        assert "time.now" in offered  # built-ins are still there
    assert external_tool("notes.search") is None


def test_external_arguments_are_checked_against_the_servers_schema() -> None:
    with external_tool_scope([_external()]):
        validate_tool_arguments("notes.search", {"q": "milk"})
        with pytest.raises(ValueError, match="required"):
            validate_tool_arguments("notes.search", {})
        with pytest.raises(ValueError, match="q"):
            validate_tool_arguments("notes.search", {"q": 3})
        with pytest.raises(ValueError, match="Additional properties"):
            validate_tool_arguments("notes.search", {"q": "x", "extra": 1})
    with pytest.raises(ValueError, match="Unknown tool"):
        validate_tool_arguments("notes.search", {"q": "milk"})


def test_cloud_tool_names_round_trip() -> None:
    with external_tool_scope([_external()]):
        names = [tool["function"]["name"] for tool in cloud_tools_openai()]
    assert "notes__search" in names
    assert decode_tool_name("notes__search") == "notes.search"


# --- policy -----------------------------------------------------------------------


def _decide(tmp_path: Path, tool: ExternalTool, **arguments: Any) -> str:
    with external_tool_scope([tool]):
        return OdessyPolicy(workspace_root=tmp_path).evaluate(ToolRequest(name=tool.public_name, arguments=arguments)).action


TRUSTED = ServerConfig(name="notes", transport="http", url="http://x/mcp", trust="trusted")


@pytest.mark.parametrize(
    ("server", "hints", "expected"),
    [
        # Untrusted: every call is confirmed, whatever the server claims.
        (NOTES, {"read_only": True}, "confirm"),
        (NOTES, {}, "confirm"),
        # Trusted: read-only and not destructive runs; anything else is confirmed.
        (TRUSTED, {"read_only": True}, "allow"),
        (TRUSTED, {"read_only": True, "destructive": False}, "allow"),
        (TRUSTED, {"read_only": True, "destructive": True}, "confirm"),
        (TRUSTED, {"destructive": True}, "confirm"),
        (TRUSTED, {}, "confirm"),
        (TRUSTED, {"read_only": False}, "confirm"),
    ],
)
def test_trust_and_annotations(tmp_path: Path, server: ServerConfig, hints: dict[str, bool], expected: str) -> None:
    assert _decide(tmp_path, _external(server=server, **hints), q="x") == expected


def test_the_config_is_the_only_thing_that_relaxes_or_forbids(tmp_path: Path) -> None:
    allow = ServerConfig(name="notes", transport="http", url="http://x/mcp", tool_policies={"search": "allow"})
    deny = ServerConfig(name="notes", transport="http", url="http://x/mcp", trust="trusted", tool_policies={"search": "deny"})
    confirm = ServerConfig(name="notes", transport="http", url="http://x/mcp", trust="trusted", tool_policies={"search": "confirm"})
    assert _decide(tmp_path, _external(server=allow), q="x") == "allow"
    assert _decide(tmp_path, _external(server=deny, read_only=True), q="x") == "deny"
    assert _decide(tmp_path, _external(server=confirm, read_only=True), q="x") == "confirm"


def test_declared_path_arguments_stay_in_the_workspace_even_when_allowed(tmp_path: Path) -> None:
    server = ServerConfig(
        name="notes", transport="http", url="http://x/mcp", workspace_paths=("path",), tool_policies={"search": "allow"}
    )
    schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    tool = _external(server=server, input_schema=schema)
    assert _decide(tmp_path, tool, path="notes/a.md") == "allow"
    assert _decide(tmp_path, tool, path="../outside.md") == "deny"
    assert _decide(tmp_path, tool, path=".env") == "deny"


def test_sandbox_arguments_are_refused_unless_the_tool_really_has_them(tmp_path: Path) -> None:
    assert _decide(tmp_path, _external(), q="x", sandbox_permissions="danger-full-access") == "deny"
    schema = {"type": "object", "properties": {"justification": {"type": "string"}}}
    assert _decide(tmp_path, _external(input_schema=schema), justification="a reason") == "confirm"


# --- the agent loop ----------------------------------------------------------------


def _fake_notes_server() -> MCPServer:
    server = MCPServer("notes")
    calls: list[str] = []
    server.calls = calls  # type: ignore[attr-defined]

    def search(q: str) -> str:
        calls.append(f"search:{q}")
        return f"Found 1 note about {q}."

    def delete_note(title: str) -> str:
        calls.append(f"delete:{title}")
        return "deleted"

    server.add_tool(search, name="search", annotations=ToolAnnotations(read_only_hint=True), structured_output=False)
    server.add_tool(delete_note, name="delete_note", annotations=ToolAnnotations(destructive_hint=True), structured_output=False)
    return server


class _InProcess(NamespacedServer):
    """A configured server whose connection is an in-process MCP server."""

    def __init__(self, config: ServerConfig, server: MCPServer) -> None:
        object.__setattr__(self, "config", config)
        object.__setattr__(self, "_server", server)

    def connection(self) -> Any:
        return self._server


class _Script:
    def __init__(self, actions: list[object]) -> None:
        self.actions = list(actions)
        self.observations: list[Any] = []

    def propose(self, user_text: str) -> Any:
        return self.actions.pop(0)

    def respond_to_observation(self, user_text: str, observation: Any) -> Any:
        self.observations.append(observation)
        return self.actions.pop(0) if self.actions else FinalResponse(text="done")


async def _discovered(namespaced: _InProcess) -> list[ExternalTool]:
    async def lister(server: ServerConfig) -> list[Any]:
        from mcp import Client

        async with Client(namespaced.connection()) as client:
            listed = await client.list_tools()
        return list(listed.tools)

    tools, _, problems = await discover_tools([namespaced.config], limit=10, list_tools=lister)
    assert not problems
    return tools


@pytest.mark.anyio
async def test_a_trusted_read_only_tool_runs_under_its_own_name_and_returns_text(tmp_path: Path) -> None:
    backend = _fake_notes_server()
    namespaced = _InProcess(TRUSTED, backend)
    confirmations: list[str] = []
    model = _Script([ToolRequest(name="notes.search", arguments={"q": "milk"}), FinalResponse(text="One note.")])
    with external_tool_scope(await _discovered(namespaced)):
        agent = OdessyAgent(
            model=model,
            policy=OdessyPolicy(workspace_root=tmp_path),
            mcp_server=(ExecutionTarget(namespaced),),
            confirm=lambda decision, request: confirmations.append(request.name) or True,
        )
        result = await agent.run_turn("find my milk note")

    assert backend.calls == ["search:milk"]  # routed to the server's own name
    assert confirmations == []
    assert model.observations[0].result == {"status": "ok", "content": "Found 1 note about milk."}
    assert result.final_text == "One note."


@pytest.mark.anyio
async def test_a_destructive_tool_waits_for_the_user_and_does_not_run_on_no(tmp_path: Path) -> None:
    backend = _fake_notes_server()
    namespaced = _InProcess(TRUSTED, backend)
    confirmations: list[str] = []
    model = _Script([ToolRequest(name="notes.delete_note", arguments={"title": "milk"})])
    with external_tool_scope(await _discovered(namespaced)):
        agent = OdessyAgent(
            model=model,
            policy=OdessyPolicy(workspace_root=tmp_path),
            mcp_server=(ExecutionTarget(namespaced),),
            confirm=lambda decision, request: confirmations.append(request.name) or False,
        )
        with pytest.raises(PermissionError):
            await agent.run_turn("delete the milk note")

    assert confirmations == ["notes.delete_note"]
    assert backend.calls == []


@pytest.mark.anyio
async def test_a_server_that_is_not_a_declared_target_never_executes(tmp_path: Path) -> None:
    backend = _fake_notes_server()
    namespaced = _InProcess(TRUSTED, backend)
    model = _Script([ToolRequest(name="notes.search", arguments={"q": "milk"})])
    with external_tool_scope(await _discovered(namespaced)):
        agent = OdessyAgent(
            model=model,
            policy=OdessyPolicy(workspace_root=tmp_path),
            mcp_server=(namespaced,),  # reachable, but not wrapped in ExecutionTarget
            confirm=True,
        )
        with pytest.raises(PermissionError, match="not a declared execution target"):
            await agent.run_turn("find my milk note")
    assert backend.calls == []


# --- the CLI ------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_cli_adds_configured_servers_as_execution_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(
        tmp_path,
        '[servers.notes]\ntransport = "http"\nurl = "http://127.0.0.1:9000/mcp"\n'
        '[servers.down]\ntransport = "http"\nurl = "http://127.0.0.1:1/mcp"\n',
    )
    monkeypatch.setenv("ODESSY_SERVERS_FILE", str(path))

    async def fake_discover(configs: list[ServerConfig], *, limit: int):
        assert limit == cli.MAX_EXTERNAL_TOOLS_CLOUD
        notes = next(c for c in configs if c.name == "notes")
        tool, _ = tool_from_mcp(notes, _tool("search"))
        return [tool], [notes], ["down: not available (ConnectionRefusedError: refused)"]

    monkeypatch.setattr(cli, "discover_tools", fake_discover)
    async with cli._tool_servers(tmp_path, cloud=True) as servers:
        targets = [item.server for item in servers if isinstance(item, ExecutionTarget)]
        names = [target.config.name for target in targets if isinstance(target, NamespacedServer)]
        assert names == ["notes"]
        assert external_tool("notes.search") is not None
    assert external_tool("notes.search") is None
    assert "external server down: not available" in capsys.readouterr().err


@pytest.mark.anyio
async def test_a_broken_servers_file_stops_with_its_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODESSY_SERVERS_FILE", str(_write(tmp_path, "[servers.git]\ntransport = 'http'\nurl = 'http://x/mcp'\n")))
    with pytest.raises(SystemExit, match="reserved"):
        async with cli._tool_servers(tmp_path):
            pass


@pytest.mark.anyio
async def test_without_a_servers_file_nothing_changes(tmp_path: Path) -> None:
    async with cli._tool_servers(tmp_path) as servers:
        assert not [s for s in servers if isinstance(getattr(s, "server", None), NamespacedServer)]


def test_the_confirmation_prompt_names_the_server(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from odessy.runtime.types import PolicyDecision

    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    with external_tool_scope([_external()]):
        cli._confirm_tool_call(
            PolicyDecision("confirm", "notes.search requires confirmation because the notes server is not marked trusted", "medium"),
            ToolRequest(name="notes.search", arguments={"q": "x"}),
        )
    assert "Server: notes (untrusted), its tool 'search'" in capsys.readouterr().out
