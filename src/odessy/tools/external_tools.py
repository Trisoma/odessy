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
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
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
    #: Argument names that hold paths; they are checked against the workspace.
    workspace_paths: tuple[str, ...] = ()
    timeout: int = DEFAULT_SERVER_TIMEOUT_SECONDS

    def connection(self, environ: Mapping[str, str] | None = None) -> Any:
        """What ``mcp.Client`` connects to: a URL, or the parameters to launch a command."""

        if self.transport == "http":
            return self.url
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

    @property
    def prefix(self) -> str:
        return self.config.name

    def connection(self) -> Any:
        return self.config.connection()


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
    if name in RESERVED_SERVER_NAMES:
        raise fail("name is reserved for a built-in tool family")

    known = {"transport", "url", "command", "env", "trust", "tools", "workspace_paths", "timeout"}
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

    return ServerConfig(
        name=name,
        transport=transport,
        url=url,
        command=tuple(command),
        env=tuple(env),
        trust=trust,
        tool_policies=policies,
        workspace_paths=tuple(workspace_paths),
        timeout=timeout,
    )


# --- discovery --------------------------------------------------------------------


def public_name(server: str, remote: str) -> str:
    return f"{server}.{remote}"


def _depth(value: Any, level: int = 0) -> int:
    if isinstance(value, dict):
        return max((_depth(item, level + 1) for item in value.values()), default=level + 1)
    if isinstance(value, list):
        return max((_depth(item, level + 1) for item in value), default=level + 1)
    return level


def tool_from_mcp(server: ServerConfig, tool: Any) -> tuple[ExternalTool | None, str | None]:
    """Turn one listed MCP tool into an ``ExternalTool``, or say why it is left out."""

    remote = str(getattr(tool, "name", "") or "")
    if not _REMOTE_TOOL_NAME.fullmatch(remote) or "__" in remote:
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
