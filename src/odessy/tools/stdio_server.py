"""Serve Odessy's tool surface over stdio for the Harness runtime (prompt 09).

The Harness spawns this process as the tool pipeline for one session
(``session/new`` → ``mcpServers``), so this is where a Harness-initiated tool call arrives.
Everything below that arrives here runs through **Odessy's** authority policy and
**Odessy's** `CommandBridge`: the same decision code and the same confinement providers the
legacy agent loop uses. Nothing about the tool surface is re-implemented for the Harness.

Two rules make this a boundary rather than a pass-through:

* the confinement is fixed at launch from the session's resolved ``SandboxPlan`` and is not a
  tool argument — there is no agent loop on this path to strip what a model supplied, so a
  call that tries to set it is refused outright rather than quietly ignored;
* every call is put to the authority policy first. An `allow` runs; anything else is refused
  with the reason in the result, because this transport has no channel back to a person
  (consent for a Harness turn is prompt 07's `session/request_permission`, and a confirmed
  operation must not become an allowed one just because it arrived here).

Usage (built from a ``SandboxPlan``; not intended to be typed by hand)::

    python -m odessy.tools.stdio_server --workspace <dir> --mode workspace-write \
        --isolation required --network deny --hidden <path> [--hidden <path>]...

Exit is stdin EOF: the Harness closes the pipe when the session ends, and the server then
returns instead of outliving its session.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Any

from odessy.authority.command_bridge import terminate_process_tree
from odessy.authority.tool_control import ToolClient
from odessy.authority.policy import OdessyPolicy
from odessy.authority.redaction import looks_like_secret_name
from odessy.authority.action_approval import build_action_approval
from odessy.config.sandbox import SandboxSettings
from odessy.execution.registry import host_registry
from odessy.tools.catalog import target_of as _target
from odessy.tools.mcp_server import SessionPolicy, build_odessy_mcp_server
from odessy.runtime.types import PolicyDecision, ToolRequest

__all__ = ["authorize", "main", "review", "scrub_environment", "session_settings"]

#: A route's credential variable is named by the profile (``apiKeyEnv``), so it is not one of
#: the well-known names: a suffix rule catches every spelling without listing them.
_SECRET_SUFFIXES = (
    "_KEY", "_APIKEY", "_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD", "_PASSWD",
    "_CREDENTIAL", "_CREDENTIALS",
)


def session_settings(*, network: str, hidden_paths: Sequence[str],
                     extra_writable: Sequence[str] = ()) -> SandboxSettings:
    """The ``sandbox.toml`` view the policy and the bridge both read.

    Built from the resolved plan rather than read from disk: the launch already made that
    decision, and a second read could disagree with it if the file changed mid-session.
    """

    return SandboxSettings(
        isolation=None,
        network_read_only=network,  # type: ignore[arg-type]
        network_workspace_write=network,  # type: ignore[arg-type]
        hidden_paths=tuple(hidden_paths),
        extra_writable=tuple(extra_writable),
        limits=None,
    )


def scrub_environment(environ: MutableMapping[str, str] | None = None) -> tuple[str, ...]:
    """Drop secrets this process inherited, and return the names dropped.

    The Harness spawns this server as a child, so it starts with whatever the runtime's
    environment holds — including the live-text bridge token and the selected route's model
    credential. This process is a tool pipeline: it needs neither, and a command it spawns
    must never be able to read them from its own environment either. Names only are returned,
    never values.
    """

    source = os.environ if environ is None else environ
    dropped = tuple(sorted(
        name for name in list(source)
        if looks_like_secret_name(name)
        or name.upper().endswith(_SECRET_SUFFIXES)
        or name.startswith("ODESSY_BRIDGE")
    ))
    for name in dropped:
        source.pop(name, None)
    return dropped


def review(policy: OdessyPolicy, name: str, arguments: Mapping[str, Any]):
    """The policy decision, and what the caller must do about it.

    Returns ``(decision, verdict)`` where ``verdict`` is ``None`` to run, a string refusal, or
    ``("confirm", details)`` — the same approval projection every other Odessy approval uses.
    ``authorize`` is this function's verdict alone; the upstream gateway also needs the
    decision itself, so that an audit record can name the rule that produced it.
    """

    request = ToolRequest(name, dict(arguments))
    decision: PolicyDecision = policy.evaluate(request)
    if decision.action == "allow":
        return decision, None
    if decision.action == "confirm":
        plan = build_action_approval(request, decision, policy, call_id="mcp-tool-call",
                                     execution_target={"host": _target(name),
                                                       "runtime": "harness"})
        return decision, ("confirm", plan.public_dict())
    return decision, f"refused by policy ({decision.rule}): {decision.reason}"


def authorize(policy: OdessyPolicy, name: str, arguments: Mapping[str, Any]):
    """``None`` to run, ``("confirm", details)`` to ask, or a refusal reason.

    `allow` executes. `confirm` returns the same approval projection every other Odessy
    approval uses, so the caller can put it to a person through the application's own broker;
    without a consent channel it does not run. `deny` is refused with its rule.
    """

    return review(policy, name, arguments)[1]


def build_server(*, workspace: Path, policy: OdessyPolicy, session: SessionPolicy,
                 consent=None, on_spawn=None):
    """The reviewed Odessy tool surface, guarded and confined for one session."""

    return build_odessy_mcp_server(
        workspace_root=workspace,
        registry=host_registry(),
        policy=None,
        include_terminal=True,
        session_policy=session,
        authorize=lambda name, arguments: authorize(policy, name, arguments),
        consent=consent,
        on_spawn=on_spawn,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="odessy.tools.stdio_server")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--mode", default="read-only")
    parser.add_argument("--isolation", default="preferred")
    parser.add_argument("--network", default="allow")
    parser.add_argument("--hidden", action="append", default=[])
    parser.add_argument("--extra-writable", action="append", default=[])
    parser.add_argument("--control-port", type=int, default=0)
    args = parser.parse_args(list(argv) if argv is not None else None)

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"odessy-stdio: workspace is not a directory: {workspace}", file=sys.stderr)
        return 2
    # Before anything else can start a child: this process is a tool pipeline, not a client.
    control_token = os.environ.pop("ODESSY_TOOL_CONTROL_TOKEN", "")
    dropped = scrub_environment()
    if dropped:
        print(f"odessy-stdio: dropped inherited secrets: {', '.join(dropped)}", file=sys.stderr)
    settings = session_settings(network=args.network, hidden_paths=args.hidden,
                                extra_writable=args.extra_writable)
    policy = OdessyPolicy(workspace, sandbox=settings, isolation=args.isolation)
    session = SessionPolicy(mode=args.mode, isolation=args.isolation, network=args.network,
                            hidden_paths=tuple(args.hidden),
                            extra_writable=tuple(args.extra_writable))

    client = ToolClient(args.control_port, control_token) if args.control_port else None
    def checked_authorize(name, arguments):
        if client is not None and not client.enter():
            return "the turn is cancelled or the control channel is unavailable"
        return authorize(policy, name, arguments)
    server = build_odessy_mcp_server(
        workspace_root=workspace, registry=host_registry(), include_terminal=True,
        session_policy=session, authorize=checked_authorize,
        consent=client.consent if client else None,
        on_spawn=client.on_spawn if client else None,
        before_spawn=client.before_spawn if client else None)
    asyncio.run(server.run_stdio_async())
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
