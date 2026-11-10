"""One upstream MCP server behind Odessy's policy and consent (prompt 09a).

Why this process exists
-----------------------

The pinned Harness can attach MCP servers (``session/new`` -> ``mcpServers``) but it has no
way to apply *Odessy's* authority to a call it forwards: it does not know which paths a
tool touches, which workspace applies, which rule denies a command, or who may approve it.
Attaching the Node tool server or a third-party server directly would therefore hand the
model a side-effecting surface with nothing in front of it.

So each upstream gets this gateway as its MCP server instead. The gateway owns the upstream
as a child process, mirrors its roster (names and input schemas verbatim) and runs every
call through the same code the Harness path already uses for its own tools:

* ``odessy.authority.policy`` decides (the Node tool names are ``BUILTIN_TOOLS``; a
  third-party tool is resolved through the existing ``ExternalTool`` conversion);
* ``odessy.tools.stdio_server.review`` produces the same approval projection every other
  Odessy approval uses, and the session's ``InteractionBroker`` answers through the existing
  ``authority.tool_control`` channel;
* ``odessy.authority.audit`` records the decision and the outcome when a log is configured.

There is no registry, scheduler, retry loop or transcript here: the roster is the child's
own ``tools/list``, the dispatch is one ``call_tool`` per request, and the lifecycle is the
child process the MCP client owns.

What is deliberately *not* exposed
----------------------------------

The Node server names its command tool by deployment (``terminal.run`` or
``laptop.terminal.run``). That tool takes its confinement mode as a **call argument**, which
is a model-settable confinement on this path, so it is not exposed. Commands remain the job
of Odessy's own MCP server (``terminal.run``), whose mode is fixed by the session plan. The
filter is by name and is reported on stderr and in the roster.

Stop and cancellation
---------------------

A gateway call outlives nothing: the runtime's Stop revokes the turn generation on the
control channel, a watcher notices it within ~50 ms, and the in-flight MCP request is
cancelled. The tool may already have acted before it was cancelled, so the result says so
(``isError`` with an explicit "unknown" wording) instead of pretending nothing happened.
The upstream process itself stays alive for the session; closing the session closes it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from odessy.authority.audit import ExternalAuditLog, audit_scope
from odessy.authority.environment import MCP_SERVER_ENV, inherit
from odessy.authority.policy import OdessyPolicy
from odessy.authority.tool_control import ToolClient
from odessy.runtime.types import ToolRequest
from odessy.tools.external_tools import (
    ExternalSessions,
    ExternalTool,
    NamespacedServer,
    ServerConfig,
    ServerConfigError,
    external_tool_scope,
    load_server_configs,
    tool_from_mcp,
)
from odessy.tools.stdio_server import review, scrub_environment, session_settings

__all__ = [
    "NODE_KIND",
    "EXTERNAL_KIND",
    "NOT_EXPOSED",
    "Upstream",
    "build_upstream",
    "main",
    "poll_seconds",
]

NODE_KIND = "node"
EXTERNAL_KIND = "external"

#: Tools the upstream may serve that this gateway will not offer, with the reason.
#: Reported rather than silently dropped, because a roster that differs from the server's
#: own is something a reader must be able to explain.
NOT_EXPOSED: dict[str, str] = {
    "terminal.run": "Odessy's own terminal.run serves commands with the session's confinement",
    "laptop.terminal.run": "Odessy's own terminal.run serves commands with the session's confinement",
}

#: How often an in-flight call checks whether the turn was revoked. The existing tool
#: channel polls at the same interval; nothing here is a latency claim.
_POLL_SECONDS = 0.05

#: The Node tool server is started with this variable naming its workspace. It travels as a
#: value (not an inherited name) so the child's environment stays an allowlist.
NODE_WORKSPACE_ENV = "ODESSY_WORKSPACE_ROOT"


def poll_seconds() -> float:
    """Exposed for tests: the revocation poll interval."""

    return _POLL_SECONDS


def _text_result(text: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], is_error=True,
    )


@dataclass
class Upstream:
    """One upstream server, its roster, and how a model-facing name maps to its own name."""

    label: str
    kind: str
    server: NamespacedServer
    #: What the model is offered, in order. Schemas are the child's own.
    roster: list[types.Tool] = field(default_factory=list)
    #: Model-facing name -> the upstream's own tool name.
    routes: dict[str, str] = field(default_factory=dict)
    #: Model-facing name -> the declared tool, for third-party servers (used by the policy).
    declared: list[ExternalTool] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "tools": sorted(self.routes),
            "skipped": dict(self.skipped),
            "upstream_trust": self.server.config.trust,
            "transport": self.server.config.transport,
        }


async def _node_upstream(*, node: Path, entry: Path, workspace: Path, label: str,
                         sessions: ExternalSessions) -> Upstream:
    """The reviewed Node tool server, launched by its built entry with a minimal env."""

    os.environ[NODE_WORKSPACE_ENV] = str(workspace)
    config = ServerConfig(
        name=label,
        transport="stdio",
        command=(str(node), str(entry)),
        # Inherited by name from this process, which sets exactly this value above.
        env=(NODE_WORKSPACE_ENV,),
        # Odessy's own reviewed code: the per-call policy below still decides every call.
        trust="trusted",
    )
    upstream = Upstream(
        label=label, kind=NODE_KIND, server=NamespacedServer(config, sessions=sessions))
    for tool in await upstream.server.list_tools():
        name = str(getattr(tool, "name", "") or "")
        if not name:
            continue
        if name in NOT_EXPOSED:
            upstream.skipped[name] = NOT_EXPOSED[name]
            continue
        upstream.roster.append(tool)
        upstream.routes[name] = name
    return upstream


async def _external_upstream(*, config: ServerConfig, label: str,
                             sessions: ExternalSessions, environ: Mapping[str, str] | None = None) -> Upstream:
    """A user-configured third-party server, exposed as ``<server>.<tool>``."""

    upstream = Upstream(
        label=label, kind=EXTERNAL_KIND,
        server=NamespacedServer(config, sessions=sessions, environ=environ))
    for tool in await upstream.server.list_tools():
        declared, reason = tool_from_mcp(config, tool)
        if declared is None:
            remote = str(getattr(tool, "name", "") or "")
            upstream.skipped[remote or "<unnamed>"] = str(reason or "not usable")
            continue
        upstream.declared.append(declared)
        upstream.routes[declared.public_name] = declared.remote_name
        upstream.roster.append(types.Tool(
            name=declared.public_name,
            description=declared.description,
            input_schema=dict(declared.input_schema),
        ))
    return upstream


async def build_upstream(*, kind: str, label: str, workspace: Path,
                         sessions: ExternalSessions,
                         node: Path | None = None, entry: Path | None = None,
                         config: ServerConfig | None = None,
                         environ: Mapping[str, str] | None = None) -> Upstream:
    if kind == NODE_KIND:
        if node is None or entry is None:
            raise ValueError("the node gateway needs an executable and a built entry")
        return await _node_upstream(node=node, entry=entry, workspace=workspace,
                                    label=label, sessions=sessions)
    if kind == EXTERNAL_KIND:
        if config is None:
            raise ValueError("the external gateway needs the server's configuration")
        return await _external_upstream(config=config, label=label, sessions=sessions, environ=environ)
    raise ValueError(f"unknown gateway kind {kind!r}")


class _Control:
    """Control-channel traffic on one thread.

    ``ToolClient`` keeps the turn generation in thread-local state, because that is how the
    existing tool server uses it (one thread per MCP handler there). An asyncio handler has
    no such thread, so every control call goes through this single-threaded executor: the
    generation is then read and written by exactly one thread, and admission keeps meaning
    what it meant on the command path.
    """

    def __init__(self, client: ToolClient) -> None:
        self._client = client
        self._executor = None

    def _thread(self):
        if self._executor is None:
            from concurrent.futures import ThreadPoolExecutor

            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gateway-control")
        return self._executor

    async def call(self, method: str, *arguments: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._thread(), getattr(self._client, method), *arguments)

    async def admit(self) -> tuple[bool, int]:
        """Whether a turn is active, and the generation this call is admitted into.

        ``enter`` also records that generation for the consent request that follows, so the
        yes and the call provably belong to the same turn.
        """

        def read() -> tuple[bool, int]:
            active = self._client.enter()
            return active, int(getattr(self._client.local, "epoch", -1))

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._thread(), read)

    async def state(self) -> tuple[bool, int]:
        def read() -> tuple[bool, int]:
            state = self._client.request(kind="state")
            return bool(state.get("active")), int(state.get("epoch", -1))

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._thread(), read)

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False)


async def _watch_revocation(control: _Control, task: "asyncio.Task[Any]", epoch: int) -> bool:
    """True once the turn this call belongs to was revoked while it was in flight.

    The comparison is to the generation the call was *admitted into*: a later turn being
    active is not this call's turn, so it cannot extend this call's life.
    """

    while not task.done():
        try:
            active, current = await control.state()
        except Exception:  # noqa: BLE001 - an unreachable channel is not a live turn
            return True
        if not active or current != epoch:
            return True
        await asyncio.sleep(_POLL_SECONDS)
    return False


def _audit_decision(log: ExternalAuditLog | None, upstream: Upstream, name: str,
                    arguments: Mapping[str, Any], decision: Any, confirmed: bool | None,
                    policy: OdessyPolicy) -> None:
    if log is None:
        return
    external = next((item for item in upstream.declared if item.public_name == name), None)
    log.decision(
        server=upstream.server.config if external is not None else None,
        remote_name=upstream.routes.get(name, name),
        arguments=arguments,
        decision=decision,
        confirmed=confirmed,
        execution=policy.execution_plan(ToolRequest(name, dict(arguments)), decision),
    )


async def _dispatch(upstream: Upstream, control: _Control | None, name: str,
                    arguments: dict[str, Any], policy: OdessyPolicy,
                    log: ExternalAuditLog | None) -> types.CallToolResult:
    """Admit, decide, consent, forward — in that order, failing closed at every step.

    Admission comes first, exactly as ``checked_authorize`` does on the command path: a call
    that arrives outside a live turn is refused before the policy is even asked, so a stale
    registration cannot be used to reach a tool.
    """

    epoch = -1
    if control is not None:
        try:
            active, epoch = await control.admit()
        except Exception:  # noqa: BLE001 - an unreachable channel is a refusal
            return _text_result("the control channel is unavailable")
        if not active:
            return _text_result("the turn is cancelled or the control channel is unavailable")

    decision, verdict = review(policy, name, arguments)

    if isinstance(verdict, str):
        _audit_decision(log, upstream, name, arguments, decision, False, policy)
        return _text_result(verdict)

    confirmed: bool | None = None
    if isinstance(verdict, tuple):
        details = verdict[1]
        if control is None:
            _audit_decision(log, upstream, name, arguments, decision, False, policy)
            return _text_result(
                "this operation requires confirmation, and no consent channel is attached "
                "to this tool server")
        approved, reason = await control.call("consent", details)
        confirmed = approved is True
        _audit_decision(log, upstream, name, arguments, decision, confirmed, policy)
        if not confirmed:
            return _text_result(f"not approved: {reason}")
    else:
        _audit_decision(log, upstream, name, arguments, decision, None, policy)

    remote = upstream.routes[name]
    forward = asyncio.create_task(
        upstream.server.call_tool(remote, arguments))
    if control is None:
        return _forwarded(await forward, name)

    watcher = asyncio.create_task(_watch_revocation(control, forward, epoch))
    try:
        done, _ = await asyncio.wait({forward, watcher}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        watcher.cancel()
        with contextlib.suppress(BaseException):
            await watcher
    if forward in done:
        return _forwarded(forward.result(), name)
    # Revoked mid-call: the request is abandoned, and the honest answer says the effect is
    # unknown rather than "not done".
    forward.cancel()
    with contextlib.suppress(BaseException):
        await forward
    return _text_result(
        f"{name} was cancelled while it was running. The server may already have acted; "
        "this result is unknown, not a rollback.")


def _forwarded(result: Any, name: str) -> types.CallToolResult:
    """The upstream's own result, unchanged: its content, bounds and error shape."""

    if isinstance(result, types.CallToolResult):
        return result
    content = list(getattr(result, "content", []) or [])
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
    if not content and structured is None:
        return _text_result(f"{name} returned no content")
    return types.CallToolResult(content=content, structured_content=structured, is_error=is_error)


async def _discard_upstream(upstream: Upstream) -> None:
    """Drop a pooled connection that a failed call may have left unusable.

    ``NamespacedServer`` keeps its session when the server answers with an MCP *error*, which
    is right for an error answer. A server that died is a different case: the next call has
    to start it again rather than reuse a closed pipe, so the session is discarded here.
    """

    sessions = upstream.server.sessions
    if sessions is None:
        return
    with contextlib.suppress(Exception):
        await sessions.discard(upstream.server.config.name)


async def _serve(*, upstream: Upstream, policy: OdessyPolicy, control: _Control | None,
                 log: ExternalAuditLog | None) -> None:
    async def on_list_tools(_ctx, _params):  # noqa: ANN001 - protocol shape
        return types.ListToolsResult(tools=list(upstream.roster))

    async def on_call_tool(_ctx, params):  # noqa: ANN001 - protocol shape
        name = str(getattr(params, "name", "") or "")
        if name not in upstream.routes:
            return _text_result(f"this server does not serve {name}")
        arguments = dict(getattr(params, "arguments", None) or {})
        try:
            return await _dispatch(upstream, control, name, arguments, policy, log)
        except asyncio.TimeoutError:
            await _discard_upstream(upstream)
            return _text_result(
                f"{name} did not answer within the server's timeout. Whether it acted is "
                "unknown, not a rollback.")
        except Exception as error:  # noqa: BLE001 - a server failure is a structured result
            await _discard_upstream(upstream)
            return _text_result(f"{name} failed: {type(error).__name__}: {error}")

    server = Server(
        f"odessy-guard-{upstream.label}",
        version="0.1.0",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="odessy.tools.upstream_gateway")
    parser.add_argument("--kind", required=True, choices=(NODE_KIND, EXTERNAL_KIND))
    parser.add_argument("--label", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--mode", default="read-only")
    parser.add_argument("--isolation", default="preferred")
    parser.add_argument("--network", default="allow")
    parser.add_argument("--hidden", action="append", default=[])
    parser.add_argument("--extra-writable", action="append", default=[])
    parser.add_argument("--control-port", type=int, default=0)
    parser.add_argument("--node-exe", default="")
    parser.add_argument("--entry", default="")
    parser.add_argument("--servers-file", default="")
    parser.add_argument("--server", default="")
    return parser.parse_args(list(argv) if argv is not None else None)


def capture_upstream_environment(config: ServerConfig | None,
                                 source: Mapping[str, str]) -> Mapping[str, str]:
    """Freeze one server's declared credentials without retaining the Harness secrets.

    HTTP header references and stdio env names share this private connection input.
    Neither the gateway's scrubbed environment nor another server receives these values.
    """
    names = (*config.env, *config.headers.values()) if config is not None else ()
    reserved = lambda name: (name.upper().startswith(('ODESSY_TOOL_CONTROL_', 'ODESSY_BRIDGE_'))
                             or name.upper() == 'ODESSY_HARNESS_PROVIDER_KEY')
    if any(reserved(name) for name in names):
        raise ServerConfigError('upstream env/header configuration names a reserved Harness secret')
    selected = inherit(source, {*MCP_SERVER_ENV, *names})
    # Preserve spelling of header references on case-insensitive Windows environments.
    lookup = {name.upper(): value for name, value in selected.items()}
    for name in names:
        if name.upper() in lookup:
            selected[name] = lookup[name.upper()]
    return MappingProxyType(selected)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"odessy-gateway: workspace is not a directory: {workspace}", file=sys.stderr)
        return 2

    if args.isolation == 'required':
        print('odessy-gateway: required upstream process isolation is unavailable; '
              'no OS confinement is applied to this server process', file=sys.stderr)
        return 2

    config: ServerConfig | None = None
    if args.kind == EXTERNAL_KIND:
        path = Path(args.servers_file) if args.servers_file else None
        try:
            configured = load_server_configs(path)
        except ServerConfigError as error:
            print(f"odessy-gateway: {error}", file=sys.stderr)
            return 2
        config = next((item for item in configured if item.name == args.server), None)
        if config is None:
            print(f"odessy-gateway: {args.server!r} is not configured in "
                  f"{path or 'the servers file'}", file=sys.stderr)
            return 2

    # Preserve only this server's explicit credentials privately, never in globals.
    # Platform names keep the stdio executable functional; unrelated credentials vanish.
    upstream_environment = capture_upstream_environment(config, os.environ)
    control_token = os.environ.pop("ODESSY_TOOL_CONTROL_TOKEN", "")
    dropped = scrub_environment()
    if dropped:
        print(f"odessy-gateway: dropped inherited secrets: {', '.join(dropped)}", file=sys.stderr)
    settings = session_settings(network=args.network, hidden_paths=args.hidden,
                               extra_writable=args.extra_writable)
    policy = OdessyPolicy(workspace, sandbox=settings, isolation=args.isolation)

    node = Path(args.node_exe).resolve() if args.node_exe else None
    entry = Path(args.entry).resolve() if args.entry else None

    client = ToolClient(args.control_port, control_token) if args.control_port else None
    control = _Control(client) if client is not None else None
    try:
        log = ExternalAuditLog.open()
    except Exception as error:  # noqa: BLE001 - a log the user expects must not vanish
        print(f"odessy-gateway: {error}", file=sys.stderr)
        return 2

    async def run() -> int:
        async with ExternalSessions() as sessions:
            try:
                upstream = await build_upstream(
                    kind=args.kind, label=args.label, workspace=workspace,
                    sessions=sessions, node=node, entry=entry, config=config,
                    environ=upstream_environment)
            except Exception as error:  # noqa: BLE001 - startup failure is reported, then fatal
                print(f"odessy-gateway: cannot start {args.label}: "
                      f"{type(error).__name__}: {error}", file=sys.stderr)
                return 3
            described = upstream.describe()
            print(f"odessy-gateway: {described['label']} ({described['kind']}) serving "
                  f"{', '.join(described['tools']) or 'no tools'}", file=sys.stderr)
            for name, reason in described["skipped"].items():
                print(f"odessy-gateway: not offering {name}: {reason}", file=sys.stderr)
            # Only a third-party tool needs to be resolvable by the policy; Odessy's own
            # names are already classified in the tool catalog.
            scope = external_tool_scope(list(upstream.declared)) if upstream.declared \
                else contextlib.nullcontext()
            with scope, audit_scope(log):
                await _serve(upstream=upstream, policy=policy, control=control, log=log)
        return 0

    try:
        return asyncio.run(run())
    finally:
        if control is not None:
            control.close()


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
