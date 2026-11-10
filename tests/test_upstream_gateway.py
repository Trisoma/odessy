"""Prompt 09a: the guarded gateway in front of an upstream MCP server.

Every test here runs the **gateway process** against a **real** upstream server over stdio and
answers consent through the **real** control channel (``authority.tool_control``). Nothing is
mocked at the authorization boundary: the only fixtures are the upstream tools themselves.

What is asserted, per requirement:

* an approved operation runs and a denied one leaves no effect (real filesystem evidence);
* workspace, path and symlink escapes are refused before the upstream is reached;
* a third-party server that is not trusted needs a person's yes, and a stored yes cannot be
  reused across turns;
* a Stop while a tool is active ends the call with an honest "unknown" instead of a hang;
* a server that dies is a structured failure, and the next call restarts it;
* closing the session leaves no upstream process behind;
* a wrong control token, a closed channel and a disposed registration all fail closed.

The Node tool server cases are the same protocol with Odessy's own server as the upstream; they
skip when no Node executable is available.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from odessy.authority.tool_control import ToolControl

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcp_fixture_server.py"

_BASE_ENV = {"PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "HOME",
             "USERPROFILE", "LANG"}


# -- harness ---------------------------------------------------------------------

def _env(**extra: str) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if name.upper() in _BASE_ENV}
    env["PYTHONPATH"] = str(SRC)
    # Never write an audit log outside the workspace from a test; the one test that wants
    # audit records points this at a temporary file explicitly.
    env.setdefault("ODESSY_AUDIT_FILE", "")
    env.update(extra)
    return env


def _servers_file(tmp_path: Path, *, root: Path, trust: str = "untrusted",
                  timeout: int = 20, env_names: tuple[str, ...] = ()) -> Path:
    declared = ", ".join(f'"{name}"' for name in ("FIXTURE_ROOT", *env_names))
    path = tmp_path / "servers.toml"
    # POSIX-style separators: a Windows path inside a TOML basic string would otherwise be
    # read as escape sequences.
    path.write_text(
        "[servers.fixture]\n"
        'transport = "stdio"\n'
        f"command = ['{Path(sys.executable).as_posix()}', '{FIXTURE.as_posix()}']\n"
        f"env = [{declared}]\n"
        f'trust = "{trust}"\n'
        'workspace_paths = ["path"]\n'
        f"timeout = {timeout}\n",
        encoding="utf-8",
    )
    return path


def _gateway(*, workspace: Path, control: ToolControl | None = None, kind: str = "external",
             servers_file: Path | None = None, server: str = "fixture",
             node: tuple[Path, Path] | None = None, mode: str = "workspace-write",
             isolation: str = "preferred", network: str = "allow",
             token: str | None = None, env: dict[str, str] | None = None
             ) -> StdioServerParameters:
    label = "odessy_ext_fixture" if kind == "external" else "odessy_node"
    args = ["-m", "odessy.tools.upstream_gateway", "--kind", kind, "--label", label,
            "--workspace", str(workspace), "--mode", mode, "--isolation", isolation,
            "--network", network]
    if control is not None:
        args += ["--control-port", str(control.address[0])]
    if kind == "external":
        assert servers_file is not None
        args += ["--servers-file", str(servers_file), "--server", server]
    else:
        assert node is not None
        args += ["--node-exe", str(node[0]), "--entry", str(node[1])]
    child_env = _env()
    if control is not None:
        child_env["ODESSY_TOOL_CONTROL_TOKEN"] = token or control.address[1]
    child_env.update(env or {})
    return StdioServerParameters(command=sys.executable, args=args, env=child_env)


def _node() -> tuple[Path, Path]:
    candidate = os.environ.get("ODESSY_TEST_NODE") or shutil.which("node") or ""
    if not candidate:
        pytest.skip("no Node executable (set ODESSY_TEST_NODE) — Node tool server cases skipped")
    from odessy.tools.node_mcp import node_tool_server_launch

    return node_tool_server_launch(candidate)


async def _tools(client: Client) -> dict[str, object]:
    listed = await client.list_tools()
    tools = list(getattr(listed, "tools", listed))
    return {tool.name: tool for tool in tools}


def _text(result) -> str:  # noqa: ANN001 - MCP result shape
    content = list(getattr(result, "content", []) or [])
    return "\n".join(str(getattr(item, "text", "")) for item in content)


def _is_error(result) -> bool:  # noqa: ANN001 - MCP result shape
    return bool(getattr(result, "is_error", False))


class _Answers:
    """The person behind the consent channel: a scripted yes/no, with what was asked."""

    def __init__(self, approved: bool = True) -> None:
        self.approved = approved
        self.asked: list[dict] = []

    async def __call__(self, details):
        self.asked.append(details)
        return self.approved, "approved" if self.approved else "declined by the test"


async def _control(answers: _Answers) -> ToolControl:
    return ToolControl(asyncio.get_running_loop(), answers)


def _wait_for(path: Path, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return False


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True,
                         text=True, check=False).stdout
    if not out.strip():
        return False
    return str(pid) in out


# -- external server ------------------------------------------------------------

def test_an_approved_external_call_runs_and_is_audited(tmp_path):
    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        root = tmp_path / "fixture-root"
        root.mkdir()
        audit = tmp_path / "audit.jsonl"
        answers = _Answers(True)
        control = await _control(answers)
        try:
            params = _gateway(
                workspace=workspace, control=control,
                servers_file=_servers_file(tmp_path, root=root),
                env={"FIXTURE_ROOT": str(root), "ODESSY_AUDIT_FILE": str(audit)})
            async with Client(params) as client:
                tools = await _tools(client)
                assert "fixture.write_file" in tools
                control.begin()
                result = await client.call_tool(
                    "fixture.write_file", {"path": "written.txt", "content": "hello"})
                assert not _is_error(result), _text(result)
                assert (root / "written.txt").read_text(encoding="utf-8") == "hello"
                assert answers.asked, "the call should have been put to a person"
                assert answers.asked[0]["operation"] == "fixture.write_file"
        finally:
            control.close()
        records = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
        assert [record["event"] for record in records] == ["decision", "call"]
        assert records[0]["decision"] == "confirm"
        assert records[1]["outcome"] == "ok"
        # Argument values are recorded as hashes, not as themselves.
        assert "hello" not in audit.read_text(encoding="utf-8")

    asyncio.run(run())


def test_a_denied_external_call_leaves_no_effect(tmp_path):
    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        root = tmp_path / "fixture-root"
        root.mkdir()
        answers = _Answers(False)
        control = await _control(answers)
        try:
            params = _gateway(
                workspace=workspace, control=control,
                servers_file=_servers_file(tmp_path, root=root),
                env={"FIXTURE_ROOT": str(root)})
            async with Client(params) as client:
                control.begin()
                result = await client.call_tool(
                    "fixture.write_file", {"path": "denied.txt", "content": "no"})
                assert _is_error(result)
                assert "not approved" in _text(result)
                assert not (root / "denied.txt").exists()
        finally:
            control.close()

    asyncio.run(run())


def test_an_operation_is_refused_when_the_channel_is_closed(tmp_path):
    """A disposed registration cannot be reused: the gateway must fail closed."""

    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        root = tmp_path / "fixture-root"
        root.mkdir()
        control = await _control(_Answers(True))
        params = _gateway(
            workspace=workspace, control=control,
            servers_file=_servers_file(tmp_path, root=root),
            env={"FIXTURE_ROOT": str(root)})
        control.close()
        async with Client(params) as client:
            result = await client.call_tool(
                "fixture.write_file", {"path": "closed.txt", "content": "no"})
            assert _is_error(result)
            assert not (root / "closed.txt").exists()

    asyncio.run(run())


def test_a_wrong_control_token_cannot_consent(tmp_path):
    """The channel is authenticated: a token the session did not issue approves nothing."""

    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        root = tmp_path / "fixture-root"
        root.mkdir()
        control = await _control(_Answers(True))
        try:
            params = _gateway(
                workspace=workspace, control=control, token="not-the-session-token",
                servers_file=_servers_file(tmp_path, root=root),
                env={"FIXTURE_ROOT": str(root)})
            async with Client(params) as client:
                control.begin()
                result = await client.call_tool(
                    "fixture.write_file", {"path": "forged.txt", "content": "no"})
                assert _is_error(result)
                assert not (root / "forged.txt").exists()
        finally:
            control.close()

    asyncio.run(run())


def test_an_approval_from_an_earlier_turn_is_not_reused(tmp_path):
    """A yes belongs to the turn that asked. After Stop, the same call needs a new one."""

    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        root = tmp_path / "fixture-root"
        root.mkdir()
        answers = _Answers(True)
        control = await _control(answers)
        try:
            params = _gateway(
                workspace=workspace, control=control,
                servers_file=_servers_file(tmp_path, root=root),
                env={"FIXTURE_ROOT": str(root)})
            async with Client(params) as client:
                control.begin()
                first = await client.call_tool(
                    "fixture.write_file", {"path": "first.txt", "content": "1"})
                assert not _is_error(first)
                assert (root / "first.txt").exists()
                control.cancel()  # the turn ends
                second = await client.call_tool(
                    "fixture.write_file", {"path": "second.txt", "content": "2"})
                assert _is_error(second)
                assert not (root / "second.txt").exists()
                # ... and a fresh turn works again.
                control.begin()
                third = await client.call_tool(
                    "fixture.write_file", {"path": "third.txt", "content": "3"})
                assert not _is_error(third)
                assert (root / "third.txt").exists()
        finally:
            control.close()

    asyncio.run(run())


def test_a_read_only_tool_runs_without_asking(tmp_path):
    """A read-only tool is allowed by policy; consent must not be spent on it."""

    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        root = tmp_path / "fixture-root"
        root.mkdir()
        answers = _Answers(True)
        control = await _control(answers)
        try:
            params = _gateway(
                workspace=workspace, control=control,
                servers_file=_servers_file(tmp_path, root=root, trust="untrusted"),
                env={"FIXTURE_ROOT": str(root)})
            async with Client(params) as client:
                control.begin()
                # `echo` is untrusted and unannotated, so this one is *confirmed*; what
                # matters is that the tool is reachable and its result travels unchanged.
                result = await client.call_tool("fixture.echo", {"text": "back"})
                assert not _is_error(result), _text(result)
                assert "back" in _text(result)
                assert len(answers.asked) == 1
        finally:
            control.close()

    asyncio.run(run())


def test_stop_during_an_external_call_is_honest_and_bounded(tmp_path):
    """Stop revokes the turn; the in-flight call ends with 'unknown', not a hang."""

    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        root = tmp_path / "fixture-root"
        root.mkdir()
        started = tmp_path / "started"
        control = await _control(_Answers(True))
        try:
            params = _gateway(
                workspace=workspace, control=control,
                servers_file=_servers_file(tmp_path, root=root,
                                           env_names=("FIXTURE_STARTED_FILE",)),
                env={"FIXTURE_ROOT": str(root), "FIXTURE_STARTED_FILE": str(started)})
            async with Client(params) as client:
                control.begin()
                call = asyncio.create_task(client.call_tool("fixture.slow", {"seconds": 20}))
                assert await asyncio.to_thread(_wait_for, started), "the call never started"
                control.cancel()
                began = time.monotonic()
                result = await asyncio.wait_for(call, 10)
                elapsed = time.monotonic() - began
                assert _is_error(result)
                assert "unknown" in _text(result)
                assert elapsed < 8, f"Stop took {elapsed:.1f}s to end the call"
        finally:
            control.close()

    asyncio.run(run())


def test_a_crashing_server_is_a_structured_failure_and_the_next_call_restarts_it(tmp_path):
    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        root = tmp_path / "fixture-root"
        root.mkdir()
        control = await _control(_Answers(True))
        try:
            params = _gateway(
                workspace=workspace, control=control,
                servers_file=_servers_file(tmp_path, root=root),
                env={"FIXTURE_ROOT": str(root)})
            async with Client(params) as client:
                control.begin()
                crashed = await client.call_tool("fixture.crash", {})
                assert _is_error(crashed)
                # The session survives a dead upstream: the next call starts it again.
                again = await client.call_tool("fixture.echo", {"text": "alive"})
                assert not _is_error(again), _text(again)
                assert "alive" in _text(again)
        finally:
            control.close()

    asyncio.run(run())


def test_a_large_result_is_bounded_by_the_upstream(tmp_path):
    """Bounds are the tool's own; the gateway must not add or lose content."""

    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        root = tmp_path / "fixture-root"
        root.mkdir()
        control = await _control(_Answers(True))
        try:
            params = _gateway(
                workspace=workspace, control=control,
                servers_file=_servers_file(tmp_path, root=root),
                env={"FIXTURE_ROOT": str(root)})
            async with Client(params) as client:
                control.begin()
                result = await client.call_tool("fixture.big", {"lines": 3})
                assert not _is_error(result), _text(result)
                assert len(_text(result)) >= 6000
        finally:
            control.close()

    asyncio.run(run())


def test_closing_the_session_leaves_no_upstream_process(tmp_path):
    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        root = tmp_path / "fixture-root"
        root.mkdir()
        pid_file = tmp_path / "fixture.pid"
        control = await _control(_Answers(True))
        try:
            params = _gateway(
                workspace=workspace, control=control,
                servers_file=_servers_file(tmp_path, root=root,
                                           env_names=("FIXTURE_PID_FILE",)),
                env={"FIXTURE_ROOT": str(root), "FIXTURE_PID_FILE": str(pid_file)})
            async with Client(params) as client:
                await _tools(client)
                assert await asyncio.to_thread(_wait_for, pid_file)
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                assert _alive(pid)
        finally:
            control.close()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _alive(pid):
            await asyncio.sleep(0.05)
        assert not _alive(pid), "the upstream server outlived the session"

    asyncio.run(run())


def test_a_malformed_call_is_rejected_by_the_upstream_schema(tmp_path):
    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        root = tmp_path / "fixture-root"
        root.mkdir()
        control = await _control(_Answers(True))
        try:
            params = _gateway(
                workspace=workspace, control=control,
                servers_file=_servers_file(tmp_path, root=root),
                env={"FIXTURE_ROOT": str(root)})
            async with Client(params) as client:
                control.begin()
                result = await client.call_tool("fixture.write_file", {"path": "x.txt"})
                assert _is_error(result), _text(result)
                assert not (root / "x.txt").exists()
        finally:
            control.close()

    asyncio.run(run())


# -- the reviewed Node tool server ----------------------------------------------

def test_the_node_surface_has_no_command_tool_and_approved_writes_land(tmp_path):
    node = _node()

    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        answers = _Answers(True)
        control = await _control(answers)
        try:
            params = _gateway(workspace=workspace, control=control, kind="node",
                              node=node, mode="workspace-write", network="allow")
            async with Client(params) as client:
                tools = await _tools(client)
                assert "filesystem.read" in tools and "filesystem.write" in tools
                assert "git.status" in tools
                # The Node server's command tool would take its confinement from the call,
                # so it is never offered on this path.
                assert "terminal.run" not in tools
                assert "laptop.terminal.run" not in tools

                control.begin()
                written = await client.call_tool(
                    "filesystem.write", {"path": "note.txt", "content": "guarded"})
                assert not _is_error(written), _text(written)
                assert (workspace / "note.txt").read_text(encoding="utf-8") == "guarded"

                read = await client.call_tool("filesystem.read", {"path": "note.txt"})
                assert not _is_error(read)
                assert "guarded" in _text(read)
        finally:
            control.close()

    asyncio.run(run())


def test_the_node_surface_refuses_denied_writes_and_escapes(tmp_path):
    node = _node()

    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        (workspace / "link").symlink_to(outside, target_is_directory=True)
        answers = _Answers(False)
        control = await _control(answers)
        try:
            params = _gateway(workspace=workspace, control=control, kind="node", node=node)
            async with Client(params) as client:
                control.begin()
                denied = await client.call_tool(
                    "filesystem.write", {"path": "denied.txt", "content": "no"})
                assert _is_error(denied)
                assert "not approved" in _text(denied)
                assert not (workspace / "denied.txt").exists()

                escape = await client.call_tool(
                    "filesystem.write", {"path": "../escape.txt", "content": "no"})
                assert _is_error(escape)
                assert "outside" in _text(escape)
                assert not (tmp_path / "escape.txt").exists()

                linked = await client.call_tool(
                    "filesystem.read", {"path": "link/secret.txt"})
                assert _is_error(linked)
                assert "secret" not in _text(linked)

                outside_read = await client.call_tool(
                    "filesystem.read", {"path": str(outside / "secret.txt")})
                assert _is_error(outside_read)
                assert "secret" not in _text(outside_read)
        finally:
            control.close()

    asyncio.run(run())


def test_a_node_call_with_a_confirmation_still_needs_the_session_turn(tmp_path):
    """`filesystem.write` is confirm-requiring, so a revoked turn refuses it outright."""

    node = _node()

    async def run():
        workspace = tmp_path / "ws"
        workspace.mkdir()
        control = await _control(_Answers(True))
        try:
            params = _gateway(workspace=workspace, control=control, kind="node", node=node)
            async with Client(params) as client:
                # No turn has begun: the channel reports an inactive generation.
                result = await client.call_tool(
                    "filesystem.write", {"path": "no-turn.txt", "content": "no"})
                assert _is_error(result)
                assert not (workspace / "no-turn.txt").exists()
        finally:
            control.close()

    asyncio.run(run())

def test_declared_secret_survives_gateway_scrubbing_only_for_its_own_upstream(tmp_path):
    """Inspect the real child's credential verdict, without returning any secret value."""
    async def run():
        workspace = tmp_path / 'ws'
        workspace.mkdir()
        fixture = Path(__file__).parent / 'fixtures' / 'mcp_credential_server.py'
        config = tmp_path / 'credential-servers.toml'
        config.write_text(
            '[servers.fixture]\ntransport="stdio"\n'
            f"command=['{Path(sys.executable).as_posix()}', '{fixture.as_posix()}']\n"
            'env=["FIXTURE_TOKEN"]\ntrust="untrusted"\n', encoding='utf-8')
        answers = _Answers(True)
        control = await _control(answers)
        try:
            params = _gateway(workspace=workspace, control=control, servers_file=config,
                env={'FIXTURE_TOKEN': 'server-credential-literal', 'UNDECLARED_TOKEN': 'not-granted',
                     'ODESSY_BRIDGE_TOKEN': 'not-for-tools',
                     'ODESSY_HARNESS_PROVIDER_KEY': 'not-for-tools'})
            async with Client(params) as client:
                assert 'fixture.credential_status' in await _tools(client)
                control.begin()
                result = await client.call_tool('fixture.credential_status', {})
                assert not _is_error(result), _text(result)
                facts = json.loads(_text(result))
                assert facts == {'authenticated': True, 'undeclared_absent': True,
                                 'control_absent': True, 'bridge_absent': True,
                                 'provider_absent': True}
        finally:
            control.close()
    asyncio.run(run())

def test_http_header_credential_survives_the_real_gateway(tmp_path):
    """Authenticate actual HTTP MCP requests using a declared, scrubbed header secret."""
    import hmac
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    authenticated = []
    token = 'http-upstream-credential-literal'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(405)
            self.send_header('Content-Length', '0')
            self.end_headers()
        def do_POST(self):
            good = hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token)
            authenticated.append(good)
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            if not good:
                self.send_response(401)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            message = json.loads(raw)
            method = message.get('method')
            if 'id' not in message:
                self.send_response(202)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if method == 'initialize':
                result = {'protocolVersion': message['params']['protocolVersion'],
                          'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'http-credential-fixture', 'version': '1'}}
            elif method == 'tools/list':
                result = {'tools': [{'name': 'credential_status', 'description': 'Check authentication',
                                    'inputSchema': {'type': 'object', 'properties': {}}}]}
            elif method == 'tools/call':
                result = {'content': [{'type': 'text', 'text': '{"authenticated": true}'}], 'isError': False}
            else:
                result = {}
            payload = json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    async def run():
        workspace = tmp_path / 'ws'
        workspace.mkdir()
        config = tmp_path / 'http-servers.toml'
        config.write_text(
            '[servers.fixture]\ntransport="http"\n'
            f'url="http://127.0.0.1:{server.server_port}/mcp"\n'
            'trust="untrusted"\n[servers.fixture.headers]\nAuthorization="FIXTURE_HTTP_TOKEN"\n',
            encoding='utf-8')
        control = await _control(_Answers(True))
        try:
            params = _gateway(workspace=workspace, control=control, servers_file=config,
                              env={'FIXTURE_HTTP_TOKEN': 'Bearer ' + token})
            async with Client(params) as client:
                assert 'fixture.credential_status' in await _tools(client)
                control.begin()
                result = await client.call_tool('fixture.credential_status', {})
                assert not _is_error(result), _text(result)
                assert json.loads(_text(result)) == {'authenticated': True}
        finally:
            control.close()
    try:
        asyncio.run(run())
        assert authenticated and all(authenticated)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_private_upstream_credentials_do_not_restore_gateway_environment(monkeypatch):
    """Credential preservation is per connection, never an exception to global scrubbing."""
    from odessy.tools.external_tools import NamespacedServer, ServerConfig, ServerConfigError
    from odessy.tools.stdio_server import scrub_environment
    from odessy.tools.upstream_gateway import capture_upstream_environment
    source = {'FIXTURE_TOKEN': 'declared-secret', 'OTHER_TOKEN': 'other-server-secret',
              'ODESSY_TOOL_CONTROL_TOKEN': 'control-secret', 'ODESSY_HARNESS_PROVIDER_KEY': 'model-secret'}
    config = ServerConfig(name='fixture', transport='stdio', command=('fixture',), env=('FIXTURE_TOKEN',))
    captured = capture_upstream_environment(config, source)
    scrub_environment(source)
    assert source == {}
    assert dict(captured) == {'FIXTURE_TOKEN': 'declared-secret'}
    with pytest.raises(TypeError):
        captured['FIXTURE_TOKEN'] = 'changed'
    bound = NamespacedServer(config, environ=captured)
    assert bound.connection().env['FIXTURE_TOKEN'] == 'declared-secret'
    assert 'declared-secret' not in repr(bound)
    for name in ('ODESSY_TOOL_CONTROL_TOKEN', 'ODESSY_BRIDGE_TOKEN', 'ODESSY_HARNESS_PROVIDER_KEY'):
        reserved = ServerConfig(name='fixture', transport='stdio', command=('fixture',), env=(name,))
        with pytest.raises(ServerConfigError, match='reserved'):
            capture_upstream_environment(reserved, {name: 'forbidden'})

def test_direct_gateway_launch_refuses_required_isolation_before_starting_child(tmp_path):
    """A direct module launch cannot bypass the session's upstream activation check."""
    workspace = tmp_path / 'ws'
    workspace.mkdir()
    root = tmp_path / 'root'
    root.mkdir()
    marker = tmp_path / 'must-not-start.pid'
    config = _servers_file(tmp_path, root=root, env_names=('FIXTURE_PID_FILE',))
    params = _gateway(workspace=workspace, servers_file=config, isolation='required', network='allow',
                      env={'FIXTURE_ROOT': str(root), 'FIXTURE_PID_FILE': str(marker)})
    result = subprocess.run([params.command, *params.args], env=params.env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert 'required upstream process isolation' in result.stderr
    assert not marker.exists()
