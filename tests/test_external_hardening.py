"""Third-party MCP servers, run-time hardening: kept sessions, the audit log, doctor.

No real third-party server is contacted. An in-process MCP server stands in for one;
its config says ``stdio`` so the kept-session path is the one exercised.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

import odessy.cli as cli
from odessy.authority.audit import (
    AuditLogError,
    ExternalAuditLog,
    audit_file,
    audit_scope,
    default_audit_file,
    hash_arguments,
)
from odessy.authority.policy import OdessyPolicy
from odessy.config.doctor import Check, Probes, run_checks
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.runtime.types import FinalResponse, ToolRequest
from odessy.tools.external_tools import (
    ExternalSessions,
    ExternalTool,
    NamespacedServer,
    ServerConfig,
    external_tool_scope,
    tool_from_mcp,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


STDIO = ServerConfig(name="notes", transport="stdio", command=("notes-mcp",), trust="trusted", timeout=1)


def _backend() -> MCPServer:
    server = MCPServer("notes")
    calls: list[str] = []
    server.calls = calls  # type: ignore[attr-defined]

    def search(q: str) -> str:
        calls.append(f"search:{q}")
        return f"Found 1 note about {q}."

    async def slow(q: str) -> str:
        calls.append(f"slow:{q}")
        await asyncio.sleep(5)
        return "late"

    def delete_note(title: str) -> str:
        calls.append(f"delete:{title}")
        return "deleted"

    server.add_tool(search, name="search", annotations=ToolAnnotations(read_only_hint=True), structured_output=False)
    server.add_tool(slow, name="slow", annotations=ToolAnnotations(read_only_hint=True), structured_output=False)
    server.add_tool(delete_note, name="delete_note", annotations=ToolAnnotations(destructive_hint=True), structured_output=False)
    return server


class _Counted(NamespacedServer):
    """A configured server reached in-process, counting how often it is connected to."""

    def __init__(self, config: ServerConfig, server: MCPServer, sessions: ExternalSessions | None) -> None:
        object.__setattr__(self, "config", config)
        object.__setattr__(self, "sessions", sessions)
        object.__setattr__(self, "_server", server)
        object.__setattr__(self, "connections", [])

    def connection(self) -> Any:
        self.connections.append(1)
        return self._server


# --- kept sessions -------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_stdio_server_is_connected_once_for_the_whole_run() -> None:
    backend = _backend()
    async with ExternalSessions() as sessions:
        server = _Counted(STDIO, backend, sessions)
        assert {tool.name for tool in await server.list_tools()} == {"search", "slow", "delete_note"}
        await server.call_tool("search", {"q": "milk"})
        await server.call_tool("search", {"q": "eggs"})
    assert backend.calls == ["search:milk", "search:eggs"]
    assert len(server.connections) == 1


@pytest.mark.anyio
async def test_without_kept_sessions_each_use_connects_again() -> None:
    backend = _backend()
    server = _Counted(STDIO, backend, None)
    await server.list_tools()
    await server.call_tool("search", {"q": "milk"})
    assert len(server.connections) == 2


@pytest.mark.anyio
async def test_an_http_server_is_never_kept() -> None:
    http = ServerConfig(name="notes", transport="http", url="http://127.0.0.1:9/mcp", timeout=1)
    async with ExternalSessions() as sessions:
        server = _Counted(http, _backend(), sessions)
        await server.list_tools()
        await server.call_tool("search", {"q": "milk"})
    assert len(server.connections) == 2


@pytest.mark.anyio
async def test_a_session_used_from_another_task_still_closes_cleanly() -> None:
    # The scheduler dispatches in tasks; the connection must not belong to them.
    backend = _backend()
    sessions = ExternalSessions()
    server = _Counted(STDIO, backend, sessions)
    await asyncio.create_task(server.call_tool("search", {"q": "milk"}))
    await asyncio.create_task(server.call_tool("search", {"q": "eggs"}))
    await sessions.aclose()
    assert backend.calls == ["search:milk", "search:eggs"]
    assert len(server.connections) == 1


@pytest.mark.anyio
async def test_a_failed_call_is_not_retried_and_the_next_call_reconnects() -> None:
    backend = _backend()
    async with ExternalSessions() as sessions:
        server = _Counted(STDIO, backend, sessions)
        client = await sessions.client("notes", server.connection, timeout=1)

        async def broken(*args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("pipe closed")

        client.call_tool = broken
        with pytest.raises(ConnectionError):
            await server.call_tool("delete_note", {"title": "milk"})
        assert backend.calls == []  # not sent twice

        await server.call_tool("search", {"q": "milk"})
    assert backend.calls == ["search:milk"]
    assert len(server.connections) == 2


@pytest.mark.anyio
async def test_a_timed_out_call_closes_the_session() -> None:
    backend = _backend()
    async with ExternalSessions() as sessions:
        server = _Counted(STDIO, backend, sessions)
        with pytest.raises(asyncio.TimeoutError):
            await server.call_tool("slow", {"q": "milk"})
        await server.call_tool("search", {"q": "eggs"})
    assert len(server.connections) == 2


@pytest.mark.anyio
async def test_a_server_that_cannot_start_is_reported_and_not_kept() -> None:
    sessions = ExternalSessions()

    def connect() -> Any:
        raise FileNotFoundError("notes-mcp")

    with pytest.raises(FileNotFoundError):
        await sessions.client("notes", connect, timeout=1)
    assert sessions._sessions == {}
    await sessions.aclose()


@pytest.mark.anyio
async def test_closed_sessions_refuse_new_use() -> None:
    sessions = ExternalSessions()
    await sessions.aclose()
    with pytest.raises(RuntimeError, match="closed"):
        await sessions.client("notes", lambda: _backend(), timeout=1)


# --- the audit log -------------------------------------------------------------------


def test_the_audit_file_location() -> None:
    assert audit_file({}) == default_audit_file()
    assert audit_file({"ODESSY_AUDIT_FILE": ""}) is None
    assert audit_file({"ODESSY_AUDIT_FILE": "  "}) is None
    assert audit_file({"ODESSY_AUDIT_FILE": "/tmp/a.jsonl"}) == Path("/tmp/a.jsonl")


def test_arguments_are_recorded_as_names_and_hashes_only() -> None:
    hashed = hash_arguments({"q": "milk", "limit": 3})
    assert list(hashed) == ["limit", "q"]
    assert hashed["q"] == hashlib.sha256(b'"milk"').hexdigest()
    assert "milk" not in json.dumps(hashed)
    # Equal values hash equally whatever their key order.
    assert hash_arguments({"f": {"a": 1, "b": 2}}) == hash_arguments({"f": {"b": 2, "a": 1}})


def test_opening_the_log_creates_its_directory(tmp_path: Path) -> None:
    target = tmp_path / "state" / "odessy" / "audit.jsonl"
    log = ExternalAuditLog.open({"ODESSY_AUDIT_FILE": str(target)})
    assert log is not None and target.parent.is_dir()
    assert ExternalAuditLog.open({"ODESSY_AUDIT_FILE": ""}) is None


def test_a_log_directory_that_cannot_be_made_is_an_error_with_the_way_out(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(AuditLogError, match="ODESSY_AUDIT_FILE"):
        ExternalAuditLog.open({"ODESSY_AUDIT_FILE": str(blocker / "audit.jsonl")})


def test_a_failed_write_warns_once_and_does_not_raise(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = ExternalAuditLog(tmp_path / "missing-dir" / "audit.jsonl")
    for _ in range(2):
        log.call(server=STDIO, remote_name="search", outcome="ok", duration_ms=1.0)
    assert capsys.readouterr().err.count("cannot write the audit log") == 1


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class _Script:
    def __init__(self, actions: list[object]) -> None:
        self.actions = list(actions)

    def propose(self, user_text: str) -> Any:
        return self.actions.pop(0)

    def respond_to_observation(self, user_text: str, observation: Any) -> Any:
        return self.actions.pop(0) if self.actions else FinalResponse(text="done")


def _tools(config: ServerConfig, backend: MCPServer) -> list[ExternalTool]:
    tools = []
    for name, annotations in (
        ("search", ToolAnnotations(read_only_hint=True)),
        ("slow", ToolAnnotations(read_only_hint=True)),
        ("delete_note", ToolAnnotations(destructive_hint=True)),
    ):
        raw = SimpleNamespace(
            name=name,
            description=name,
            input_schema={"type": "object", "properties": {"q": {"type": "string"}, "title": {"type": "string"}}},
            annotations=annotations,
        )
        tool, problem = tool_from_mcp(config, raw)
        assert problem is None and tool is not None
        tools.append(tool)
    return tools


async def _turn(
    tmp_path: Path, config: ServerConfig, request: ToolRequest, *, confirm: bool
) -> tuple[MCPServer, list[dict[str, Any]], Exception | None]:
    backend = _backend()
    log_path = tmp_path / "audit.jsonl"
    error: Exception | None = None
    async with ExternalSessions() as sessions:
        server = _Counted(config, backend, sessions)
        with external_tool_scope(_tools(config, backend)), audit_scope(ExternalAuditLog(log_path)):
            agent = OdessyAgent(
                model=_Script([request, FinalResponse(text="done")]),
                policy=OdessyPolicy(workspace_root=tmp_path),
                mcp_server=(ExecutionTarget(server),),
                confirm=confirm,
            )
            try:
                await agent.run_turn("do it")
            except Exception as raised:  # noqa: BLE001 - the test inspects it
                error = raised
    records = _records(log_path) if log_path.exists() else []
    return backend, records, error


@pytest.mark.anyio
async def test_an_allowed_call_records_its_decision_then_its_outcome(tmp_path: Path) -> None:
    backend, records, error = await _turn(tmp_path, STDIO, ToolRequest("notes.search", {"q": "milk"}), confirm=False)
    assert error is None and backend.calls == ["search:milk"]
    decision, call = records
    assert decision["event"] == "decision"
    assert (decision["server"], decision["tool"], decision["trust"]) == ("notes", "search", "trusted")
    assert (decision["decision"], decision["rule"], decision["confirmed"]) == ("allow", "trusted_read_only", None)
    assert decision["arguments"] == hash_arguments({"q": "milk"})
    assert "milk" not in json.dumps(records)
    assert call["event"] == "call" and call["outcome"] == "ok" and call["duration_ms"] >= 0


@pytest.mark.anyio
async def test_a_confirmed_call_records_the_yes(tmp_path: Path) -> None:
    _, records, error = await _turn(tmp_path, STDIO, ToolRequest("notes.delete_note", {"title": "milk"}), confirm=True)
    assert error is None
    assert [(r["event"], r.get("rule"), r.get("confirmed")) for r in records] == [
        ("decision", "default_confirm", True),
        ("call", None, None),
    ]


@pytest.mark.anyio
async def test_a_declined_call_records_the_no_and_never_runs(tmp_path: Path) -> None:
    backend, records, error = await _turn(tmp_path, STDIO, ToolRequest("notes.delete_note", {"title": "milk"}), confirm=False)
    assert isinstance(error, PermissionError) and backend.calls == []
    (decision,) = records
    assert (decision["decision"], decision["rule"], decision["confirmed"]) == ("confirm", "default_confirm", False)


@pytest.mark.anyio
async def test_a_call_denied_by_config_records_the_rule(tmp_path: Path) -> None:
    config = ServerConfig(
        name="notes", transport="stdio", command=("notes-mcp",), timeout=1, tool_policies={"delete_note": "deny"}
    )
    backend, records, error = await _turn(tmp_path, config, ToolRequest("notes.delete_note", {"title": "x"}), confirm=True)
    assert isinstance(error, PermissionError) and backend.calls == []
    (decision,) = records
    assert (decision["decision"], decision["rule"], decision["trust"]) == ("deny", "config_deny", "untrusted")


@pytest.mark.anyio
async def test_a_timed_out_call_is_recorded_as_such(tmp_path: Path) -> None:
    _, records, _ = await _turn(tmp_path, STDIO, ToolRequest("notes.slow", {"q": "x"}), confirm=False)
    call = records[-1]
    assert call["event"] == "call" and call["outcome"] == "timeout" and call["error_type"] == "TimeoutError"


def test_every_external_rule_is_named(tmp_path: Path) -> None:
    policy = OdessyPolicy(workspace_root=tmp_path)
    schema = {"type": "object", "properties": {"path": {"type": "string"}}}

    def rule(config: ServerConfig, read_only: bool | None = None, destructive: bool | None = None, **arguments: Any) -> str | None:
        tool = ExternalTool("notes.search", "search", config, "d", schema, read_only=read_only, destructive=destructive)
        with external_tool_scope([tool]):
            return policy.evaluate(ToolRequest("notes.search", arguments)).rule

    base = dict(name="notes", transport="http", url="http://x/mcp")
    assert rule(ServerConfig(**base), sandbox_permissions="x") == "sandbox_arguments"
    assert rule(ServerConfig(**base, tool_policies={"search": "deny"})) == "config_deny"
    assert rule(ServerConfig(**base, workspace_paths=("path",)), path="../x") == "workspace_path"
    assert rule(ServerConfig(**base, tool_policies={"search": "allow"})) == "config_allow"
    assert rule(ServerConfig(**base, tool_policies={"search": "confirm"})) == "config_confirm"
    assert rule(ServerConfig(**base)) == "untrusted_server"
    assert rule(ServerConfig(**base, trust="trusted"), read_only=True) == "trusted_read_only"
    assert rule(ServerConfig(**base, trust="trusted"), read_only=True, destructive=True) == "default_confirm"


# --- the CLI -------------------------------------------------------------------------


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "servers.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.anyio
async def test_the_cli_gives_configured_servers_one_session_pool_and_the_audit_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ODESSY_SERVERS_FILE", str(_write(tmp_path, '[servers.notes]\ntransport = "stdio"\ncommand = ["notes-mcp"]\n')))
    monkeypatch.setenv("ODESSY_AUDIT_FILE", str(tmp_path / "audit" / "log.jsonl"))
    listed_through: list[Any] = []

    async def fake_discover(configs: list[ServerConfig], *, limit: int, list_tools: Any = None):
        listed_through.append(list_tools)
        return [], list(configs), []

    monkeypatch.setattr(cli, "discover_tools", fake_discover)
    async with cli._tool_servers(tmp_path) as servers:
        (target,) = [item.server for item in servers if isinstance(getattr(item, "server", None), NamespacedServer)]
        assert isinstance(target.sessions, ExternalSessions)
        from odessy.authority.audit import current_audit

        assert current_audit() is not None and current_audit().path == tmp_path / "audit" / "log.jsonl"
    assert listed_through[0] is not None
    assert target.sessions._closed


@pytest.mark.anyio
async def test_the_cli_stops_when_the_audit_log_cannot_be_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODESSY_SERVERS_FILE", str(_write(tmp_path, '[servers.notes]\ntransport = "http"\nurl = "http://127.0.0.1:9/mcp"\n')))
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("ODESSY_AUDIT_FILE", str(blocker / "log.jsonl"))
    with pytest.raises(SystemExit, match="audit log"):
        async with cli._tool_servers(tmp_path):
            pass


# --- doctor --------------------------------------------------------------------------


def _probes(*, on_path: set[str] = frozenset({"notes-mcp"}), listed: Any = None) -> Probes:
    def list_server_tools(config: ServerConfig) -> list[Any]:
        if isinstance(listed, Exception):
            raise listed
        return listed if listed is not None else [_raw("search"), _raw("delete_note")]

    return Probes(
        which=lambda name: f"/usr/bin/{name}" if name in on_path else None,
        http_get_json=lambda url, timeout: {"models": [{"name": "lfm2.5-thinking:1.2b-q8_0"}]},
        tcp_reachable=lambda host, port, timeout: True,
        command_output=lambda argv, timeout: "v22.11.0",
        node_tools_state=lambda: (True, True),
        path_exists=lambda path: False,
        list_server_tools=list_server_tools,
    )


def _raw(name: str) -> Any:
    return SimpleNamespace(name=name, description=name, input_schema={"type": "object"}, annotations=None)


def _servers(checks: list[Check]) -> list[Check]:
    return [check for check in checks if check.section == "Third-party servers"]


def _env(tmp_path: Path, text: str | None, **extra: str) -> dict[str, str]:
    env = {"ODESSY_DEPLOYMENT": "single", "ODESSY_SERVERS_FILE": str(tmp_path / "servers.toml"), **extra}
    if text is not None:
        _write(tmp_path, text)
    return env


STDIO_TOML = '[servers.notes]\ntransport = "stdio"\ncommand = ["notes-mcp"]\n'


def test_doctor_is_silent_without_a_servers_file(tmp_path: Path) -> None:
    assert _servers(run_checks("lfm", _env(tmp_path, None), _probes())) == []


def test_doctor_fails_a_malformed_servers_file(tmp_path: Path) -> None:
    (check,) = _servers(run_checks("lfm", _env(tmp_path, "[servers.git]\ntransport = 'http'\nurl = 'http://x/mcp'\n"), _probes()))
    assert check.status == "fail" and "reserved" in check.summary


def test_doctor_counts_a_working_servers_tools_and_names_the_audit_log(tmp_path: Path) -> None:
    checks = _servers(run_checks("lfm", _env(tmp_path, STDIO_TOML, ODESSY_AUDIT_FILE=str(tmp_path / "a.jsonl")), _probes()))
    assert [c.status for c in checks] == ["ok", "ok", "ok"]
    assert "notes (stdio, untrusted): 2 tool(s)" in checks[1].summary
    assert str(tmp_path / "a.jsonl") in checks[2].summary


def test_doctor_warns_when_a_stdio_command_is_missing(tmp_path: Path) -> None:
    checks = _servers(run_checks("lfm", _env(tmp_path, STDIO_TOML), _probes(on_path=set())))
    (warning,) = [c for c in checks if c.status == "warn"]
    assert "command not found: notes-mcp" in warning.summary and warning.fix


def test_doctor_warns_when_a_server_does_not_answer(tmp_path: Path) -> None:
    checks = _servers(run_checks("lfm", _env(tmp_path, STDIO_TOML), _probes(listed=ConnectionError("refused"))))
    (warning,) = [c for c in checks if c.status == "warn"]
    assert "not available (ConnectionError: refused)" in warning.summary
    assert "notes-mcp" in (warning.fix or "")


def test_doctor_names_tools_that_will_be_left_out(tmp_path: Path) -> None:
    checks = _servers(run_checks("lfm", _env(tmp_path, STDIO_TOML), _probes(listed=[_raw("ok"), _raw("bad__name")])))
    assert any(c.status == "warn" and "bad__name" in c.summary for c in checks)
    assert any("1 tool(s)" in c.summary for c in checks)


def test_doctor_warns_past_the_local_tool_limit(tmp_path: Path) -> None:
    many = [_raw(f"t{i}") for i in range(20)]
    checks = _servers(run_checks("lfm", _env(tmp_path, STDIO_TOML), _probes(listed=many)))
    assert any(c.status == "warn" and "only the first 16" in c.summary for c in checks)


def test_doctor_warns_when_the_audit_log_is_off(tmp_path: Path) -> None:
    checks = _servers(run_checks("lfm", _env(tmp_path, STDIO_TOML, ODESSY_AUDIT_FILE=""), _probes()))
    assert any(c.status == "warn" and "audit log is off" in c.summary for c in checks)


# --- names that survive the cloud adapters' encoding ---------------------------------


@pytest.mark.parametrize("name", ["my__notes", "notes_"])
def test_server_names_that_cannot_round_trip_are_refused(tmp_path: Path, name: str) -> None:
    from odessy.tools.external_tools import ServerConfigError, load_server_configs

    path = _write(tmp_path, f'[servers.{name}]\ntransport = "http"\nurl = "http://127.0.0.1:9/mcp"\n')
    with pytest.raises(ServerConfigError, match="'__'"):
        load_server_configs(path)


@pytest.mark.parametrize("remote", ["a_.b", "bad__name"])
def test_tool_names_that_cannot_round_trip_are_left_out(remote: str) -> None:
    from odessy.models.cloud_model import decode_tool_name, encode_tool_name

    tool, problem = tool_from_mcp(STDIO, _raw(remote))
    assert tool is None and "unsupported name" in (problem or "")
    assert decode_tool_name(encode_tool_name(f"notes.{remote}")) != f"notes.{remote}"


@pytest.mark.parametrize("remote", ["search", "_private", "list-all", "v1.search", "trailing_", "a._b"])
def test_ordinary_tool_names_are_kept(remote: str) -> None:
    from odessy.models.cloud_model import decode_tool_name, encode_tool_name

    tool, problem = tool_from_mcp(STDIO, _raw(remote))
    assert problem is None and tool is not None
    assert decode_tool_name(encode_tool_name(tool.public_name)) == tool.public_name
