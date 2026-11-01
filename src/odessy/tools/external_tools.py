"""Third-party MCP servers: configuration, discovery, and the tools they offer.

A server is usable only if the user lists it in ``servers.toml``: being reachable is
not being trusted. Each listed server's tools are offered to the model under the
server's name (``notes.search``), checked against the server's own input schema, and
authorized by ``OdessyPolicy`` with the rules in ``docs/reference/external-servers.mdx``:

* a per-tool ``policy`` in the config is the only thing that can relax a decision;
* a server is ``untrusted`` unless the config says otherwise, and every call to an
  untrusted server needs confirmation;
* what a server says about itself (descriptions, annotations) can only make a
  decision stricter, never looser, except ``read_only_hint`` on a server the user
  marked ``trusted``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib

Trust = Literal["untrusted", "trusted"]
ToolPolicy = Literal["allow", "confirm", "deny"]
Transport = Literal["http", "stdio"]

SERVERS_FILE_ENV = "ODESSY_SERVERS_FILE"

#: Server names become tool-name prefixes, so they are short, lowercase identifiers.
_SERVER_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
#: Remote tool names Odessy can carry through every adapter. Cloud adapters encode "."
#: as "__" and decode it back, so a remote name containing "__" could not round-trip.
_REMOTE_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
#: Prefixes of the built-in tools. A server may not take one, so `git.status` can
#: never mean a third-party tool.
RESERVED_SERVER_NAMES = frozenset(
    {"time", "terminal", "laptop", "calculator", "filesystem", "log", "project", "git", "odessy"}
)

#: Limits on what one server may put in front of the model. Descriptions and schemas
#: are written by the server's author and sent with every model call.
MAX_DESCRIPTION_CHARS = 600
MAX_SCHEMA_CHARS = 8000
MAX_SCHEMA_DEPTH = 8
DEFAULT_SERVER_TIMEOUT_SECONDS = 30
#: Third-party tools offered in one run. Every tool's schema is sent with every model
#: call, and a small local model chooses worse among many tools.
MAX_EXTERNAL_TOOLS_CLOUD = 64
MAX_EXTERNAL_TOOLS_LOCAL = 16
#: Seconds a stdio server gets to exit when its session is closed.
_SESSION_CLOSE_SECONDS = 5

#: Environment variables every stdio server gets, so the command can start at all.
#: Anything else must be listed in the server's `env`.
_BASE_ENV = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG")


class ServerConfigError(ValueError):
    """The servers file is malformed. Raised with the file and server named."""


@dataclass(frozen=True)
class ServerConfig:
    name: str
    transport: Transport
    url: str | None = None
    command: tuple[str, ...] = ()
    #: Environment variable names passed to a stdio server, beyond the base set.
    env: tuple[str, ...] = ()
    trust: Trust = "untrusted"
    #: Per-tool overrides, keyed by the server's own tool name.
    tool_policies: Mapping[str, ToolPolicy] = field(default_factory=dict)
    #: HTTP headers, as header name -> the environment variable holding its value.
    #: Indirection keeps a paired host's execution credential out of servers.toml;
    #: the value is read when the connection is made, never stored here.
    headers: Mapping[str, str] = field(default_factory=dict)
    #: Argument names that hold paths; they are checked against the workspace.
    workspace_paths: tuple[str, ...] = ()
    timeout: int = DEFAULT_SERVER_TIMEOUT_SECONDS
    #: Stable, non-secret facts about who this server is (a paired host's alias, for
    #: example). Carried into execution-target metadata and approval bindings.
    target_metadata: Mapping[str, str] = field(default_factory=dict)

    def connection(self, environ: Mapping[str, str] | None = None) -> Any:
        """What ``mcp.Client`` connects to: a URL, or the parameters to launch a command."""

        if self.transport == "http":
            source = os.environ if environ is None else environ
            resolved = {name: source[value] for name, value in self.headers.items()
                        if isinstance(source.get(value), str) and source.get(value)}
            if not resolved:
                return self.url
            # The credential rides an authenticated client, not the URL, so it
            # cannot leak into logs, error text or a redirect target.
            import httpx2
            from mcp.client.streamable_http import streamable_http_client

            return streamable_http_client(self.url, http_client=httpx2.AsyncClient(headers=resolved))
        from mcp.client.stdio import StdioServerParameters

        source = os.environ if environ is None else environ
        allowed = {*_BASE_ENV, *self.env}
        # Matched case-insensitively: Windows environment names are.
        upper = {name.upper() for name in allowed}
        child_env = {key: value for key, value in source.items() if key.upper() in upper}
        return StdioServerParameters(command=self.command[0], args=list(self.command[1:]), env=child_env)


@dataclass(frozen=True)
class NamespacedServer:
    """A configured third-party server. The agent offers its tools as ``<name>.<tool>``.

    Wrapped in ``ExecutionTarget`` by the composition root when it may execute; the
    agent routes ``notes.search`` to the server's own ``search``.
    """

    config: ServerConfig
    #: Sessions kept open for the run. Without it (or for an http server) each listing
    #: and call opens its own connection.
    sessions: ExternalSessions | None = field(default=None, compare=False, repr=False)

    @property
    def prefix(self) -> str:
        return self.config.name

    def connection(self) -> Any:
        return self.config.connection()

    @property
    def _pooled(self) -> bool:
        return self.sessions is not None and self.config.transport == "stdio"

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Any]:
        """A connected ``mcp.Client``: the run's kept session, or a fresh one."""

        if self._pooled:
            assert self.sessions is not None
            yield await self.sessions.client(self.config.name, self.connection, timeout=self.config.timeout)
            return
        from mcp.client import Client

        async with Client(self.connection()) as client:
            yield client

    async def list_tools(self) -> list[Any]:
        async with self.session() as client:
            result = await asyncio.wait_for(client.list_tools(), timeout=self.config.timeout)
        return list(getattr(result, "tools", result))

    async def call_tool(self, remote_name: str, arguments: dict[str, Any], *, before_dispatch=None) -> Any:
        """Call the server's own ``remote_name``, bounded by the server's timeout.

        A failed call is never retried: the server may have acted before the failure,
        and the user confirmed one call. A kept session that failed or timed out is
        closed, so the next call starts the server again.
        """

        from mcp.shared.exceptions import MCPError

        from odessy.authority.audit import current_audit

        started = time.perf_counter()
        outcome, error_type = "failed", None
        try:
            admission_error = None
            async with self.session() as client:
                async def dispatch():
                    nonlocal admission_error
                    if before_dispatch is not None:
                        try:
                            before_dispatch()
                        except PermissionError as exc:
                            admission_error = exc
                            return None
                    return await client.call_tool(remote_name, arguments)
                result = await asyncio.wait_for(dispatch(), timeout=self.config.timeout)
            if admission_error is not None:
                raise admission_error
            outcome = "error" if _is_error_result(result) else "ok"
            return result
        except asyncio.TimeoutError:
            outcome, error_type = "timeout", "TimeoutError"
            await self._discard_session()
            raise
        except MCPError as error:
            # An error answer: the session itself is fine.
            error_type = error.__class__.__name__
            raise
        except Exception as error:
            error_type = error.__class__.__name__
            await self._discard_session()
            raise
        finally:
            audit = current_audit()
            if audit is not None:
                audit.call(
                    server=self.config,
                    remote_name=remote_name,
                    outcome=outcome,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    error_type=error_type,
                )

    async def _discard_session(self) -> None:
        if self._pooled:
            assert self.sessions is not None
            await self.sessions.discard(self.config.name)


def _is_error_result(result: Any) -> bool:
    return bool(getattr(result, "is_error", False) or getattr(result, "isError", False))


class _Session:
    """One kept connection, owned by its own task.

    ``mcp.Client`` must be exited in the task that entered it. Listings and calls come
    from different tasks (the tool scheduler dispatches in tasks), so the connection
    lives in a dedicated owner task that enters it, waits to be told to close, and
    exits it.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._ready: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._closing = asyncio.Event()
        self._task = asyncio.create_task(self._own())

    async def _own(self) -> None:
        from mcp.client import Client

        try:
            async with Client(self._connection) as client:
                self._ready.set_result(client)
                await self._closing.wait()
        except BaseException as error:
            if not self._ready.done():
                self._ready.set_exception(error if isinstance(error, Exception) else ConnectionError("session cancelled"))
            if not isinstance(error, Exception):
                raise

    @property
    def alive(self) -> bool:
        return not self._task.done()

    async def client(self, timeout: float) -> Any:
        return await asyncio.wait_for(asyncio.shield(self._ready), timeout=timeout)

    async def close(self) -> None:
        self._closing.set()
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=_SESSION_CLOSE_SECONDS)
        except BaseException:  # noqa: BLE001 - closing must not raise; cancel what lingers
            self._task.cancel()
        if self._ready.done() and not self._ready.cancelled():
            self._ready.exception()  # mark retrieved: a failed start was already reported


class ExternalSessions:
    """The run's kept sessions to stdio servers, one per server, started on first use.

    Starting a stdio server for every listing and call is slow and loses whatever
    state the server keeps. A session that has ended (the server exited) is started
    again on the next use; one that failed a call is closed by the caller.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._closed = False

    async def client(self, name: str, connect: Callable[[], Any], *, timeout: float) -> Any:
        if self._closed:
            raise RuntimeError("external server sessions are closed")
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            session = self._sessions.get(name)
            if session is not None and not session.alive:
                await session.close()
                session = None
            if session is None:
                session = self._sessions[name] = _Session(connect())
            try:
                return await session.client(timeout)
            except BaseException:
                self._sessions.pop(name, None)
                await session.close()
                raise

    async def discard(self, name: str) -> None:
        session = self._sessions.pop(name, None)
        if session is not None:
            await session.close()

    async def aclose(self) -> None:
        self._closed = True
        sessions, self._sessions = list(self._sessions.values()), {}
        for session in sessions:
            await session.close()

    async def __aenter__(self) -> ExternalSessions:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


@dataclass(frozen=True)
class ExternalTool:
    public_name: str
    remote_name: str
    server: ServerConfig
    description: str
    input_schema: dict[str, Any]
    #: The server's own annotations; None when it did not say.
    read_only: bool | None = None
    destructive: bool | None = None
    open_world: bool | None = None


# --- configuration ----------------------------------------------------------------


def default_servers_file() -> Path:
    return Path.home() / ".config" / "odessy" / "servers.toml"


def servers_file(environ: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    explicit = source.get(SERVERS_FILE_ENV, "").strip()
    return Path(explicit) if explicit else default_servers_file()


def load_server_configs(path: Path | None = None, environ: Mapping[str, str] | None = None) -> list[ServerConfig]:
    """The configured servers, or an empty list when there is no servers file."""

    target = path or servers_file(environ)
    if not target.is_file():
        return []
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ServerConfigError(f"{target}: cannot read servers file: {error}") from error
    raw_servers = data.get("servers", {})
    if not isinstance(raw_servers, dict):
        raise ServerConfigError(f"{target}: [servers] must be a table")
    return [_parse_server(target, name, raw) for name, raw in raw_servers.items()]


def _parse_server(path: Path, name: str, raw: Any) -> ServerConfig:
    def fail(message: str) -> ServerConfigError:
        return ServerConfigError(f"{path}: server {name!r}: {message}")

    if not isinstance(raw, dict):
        raise fail("must be a table")
    if not _SERVER_NAME.fullmatch(name):
        raise fail("name must be lowercase letters, digits, '-' or '_', starting with a letter (max 32)")
    if not _round_trips(public_name(name, "tool")):
        raise fail("name may not contain '__' or end with '_' (cloud adapters encode '.' as '__')")
    if name in RESERVED_SERVER_NAMES:
        raise fail("name is reserved for a built-in tool family")

    known = {"transport", "url", "command", "env", "trust", "tools", "workspace_paths", "timeout",
             "headers"}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise fail(f"unknown keys {unknown}")

    transport = raw.get("transport")
    if transport not in ("http", "stdio"):
        raise fail("transport must be 'http' or 'stdio'")
    url = raw.get("url")
    command = raw.get("command", [])
    if transport == "http":
        if not isinstance(url, str) or not re.match(r"^https?://", url):
            raise fail("an http server needs url = 'http(s)://...'")
        if command:
            raise fail("an http server takes no command")
    else:
        if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
            raise fail("a stdio server needs command = ['program', 'arg', ...]")
        if url is not None:
            raise fail("a stdio server takes no url")

    env = raw.get("env", [])
    if not isinstance(env, list) or not all(isinstance(item, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in env):
        raise fail("env must be a list of environment variable names")

    trust = raw.get("trust", "untrusted")
    if trust not in ("untrusted", "trusted"):
        raise fail("trust must be 'untrusted' or 'trusted'")

    tools = raw.get("tools", {})
    if not isinstance(tools, dict):
        raise fail("[tools] must be a table of tool names")
    policies: dict[str, ToolPolicy] = {}
    for tool_name, tool_raw in tools.items():
        if not isinstance(tool_raw, dict) or set(tool_raw) - {"policy"}:
            raise fail(f"tools.{tool_name} accepts only policy = 'allow' | 'confirm' | 'deny'")
        policy = tool_raw.get("policy")
        if policy not in ("allow", "confirm", "deny"):
            raise fail(f"tools.{tool_name}.policy must be 'allow', 'confirm' or 'deny'")
        policies[tool_name] = policy

    workspace_paths = raw.get("workspace_paths", [])
    if not isinstance(workspace_paths, list) or not all(isinstance(item, str) and item for item in workspace_paths):
        raise fail("workspace_paths must be a list of argument names")

    timeout = raw.get("timeout", DEFAULT_SERVER_TIMEOUT_SECONDS)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
        raise fail("timeout must be a positive integer (seconds)")

    headers = raw.get("headers", {})
    if not isinstance(headers, dict) or not all(
            isinstance(key, str) and key and isinstance(value, str) and value
            for key, value in headers.items()):
        raise fail("headers must map a header name to an environment variable name")

    return ServerConfig(
        name=name,
        transport=transport,
        url=url,
        command=tuple(command),
        env=tuple(env),
        trust=trust,
        headers=dict(headers),
        tool_policies=policies,
        workspace_paths=tuple(workspace_paths),
        timeout=timeout,
    )


# --- discovery --------------------------------------------------------------------


def public_name(server: str, remote: str) -> str:
    return f"{server}.{remote}"


def _round_trips(name: str) -> bool:
    """Whether a public tool name survives the cloud adapters' '.' <-> '__' encoding."""

    return name.replace(".", "__").replace("__", ".") == name


def _depth(value: Any, level: int = 0) -> int:
    if isinstance(value, dict):
        return max((_depth(item, level + 1) for item in value.values()), default=level + 1)
    if isinstance(value, list):
        return max((_depth(item, level + 1) for item in value), default=level + 1)
    return level


def tool_from_mcp(server: ServerConfig, tool: Any) -> tuple[ExternalTool | None, str | None]:
    """Turn one listed MCP tool into an ``ExternalTool``, or say why it is left out."""

    remote = str(getattr(tool, "name", "") or "")
    if not _REMOTE_TOOL_NAME.fullmatch(remote) or not _round_trips(public_name(server.name, remote)):
        return None, f"{server.name}: tool {remote!r} skipped: unsupported name"
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        return None, f"{server.name}: tool {remote!r} skipped: input schema is not an object"
    if len(json.dumps(schema)) > MAX_SCHEMA_CHARS or _depth(schema) > MAX_SCHEMA_DEPTH:
        return None, f"{server.name}: tool {remote!r} skipped: input schema too large"
    try:
        from jsonschema.validators import validator_for

        validator_for(schema).check_schema(schema)
    except Exception as error:  # noqa: BLE001 - any invalid schema leaves the tool out
        return None, f"{server.name}: tool {remote!r} skipped: invalid input schema ({error.__class__.__name__})"

    description = " ".join(str(getattr(tool, "description", "") or getattr(tool, "title", "") or remote).split())
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS] + "..."
    annotations = getattr(tool, "annotations", None)

    def hint(name: str) -> bool | None:
        value = getattr(annotations, name, None) if annotations is not None else None
        return value if isinstance(value, bool) else None

    return (
        ExternalTool(
            public_name=public_name(server.name, remote),
            remote_name=remote,
            server=server,
            description=description,
            input_schema=schema,
            read_only=hint("read_only_hint"),
            destructive=hint("destructive_hint"),
            open_world=hint("open_world_hint"),
        ),
        None,
    )


async def discover_tools(
    servers: list[ServerConfig],
    *,
    limit: int,
    list_tools: Any = None,
) -> tuple[list[ExternalTool], list[ServerConfig], list[str]]:
    """List every configured server's tools.

    Returns the tools to offer (at most ``limit``, in config order), the servers that
    answered, and human-readable problems. A server that fails to answer is left out
    without affecting the others or the built-in tools.
    """

    lister = list_tools or _list_tools
    tools: list[ExternalTool] = []
    reachable: list[ServerConfig] = []
    problems: list[str] = []
    seen: set[str] = set()
    for server in servers:
        try:
            listed = await asyncio.wait_for(lister(server), timeout=server.timeout)
        except Exception as error:  # noqa: BLE001 - one broken server must not stop the run
            problems.append(f"{server.name}: not available ({error.__class__.__name__}: {error})")
            continue
        reachable.append(server)
        for raw in listed:
            tool, problem = tool_from_mcp(server, raw)
            if problem:
                problems.append(problem)
                continue
            assert tool is not None
            if tool.public_name in seen:
                problems.append(f"{server.name}: tool {tool.remote_name!r} skipped: listed twice")
                continue
            if len(tools) >= limit:
                problems.append(f"{server.name}: tool {tool.remote_name!r} skipped: more than {limit} external tools")
                continue
            seen.add(tool.public_name)
            tools.append(tool)
    return tools, reachable, problems


async def _list_tools(server: ServerConfig) -> list[Any]:
    from mcp.client import Client

    async with Client(server.connection()) as client:
        result = await client.list_tools()
    return list(getattr(result, "tools", result))


# --- the tools of this run ----------------------------------------------------------

_external_tools: ContextVar[Mapping[str, ExternalTool]] = ContextVar("external_tools", default={})


@contextmanager
def external_tool_scope(tools: list[ExternalTool]) -> Iterator[None]:
    """Make these external tools model-facing for this run (and nothing outside it)."""

    token = _external_tools.set({tool.public_name: tool for tool in tools})
    try:
        yield
    finally:
        _external_tools.reset(token)


def external_tools() -> list[ExternalTool]:
    return list(_external_tools.get().values())


def external_tool(name: str) -> ExternalTool | None:
    return _external_tools.get().get(name)
