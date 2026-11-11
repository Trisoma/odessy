"""Upstream MCP servers a Harness session may attach (prompt 09a).

Two surfaces beyond Odessy's own reviewed tool server are attached through the pin's own
``session/new`` -> ``mcpServers`` facility:

* the **Node tool server** — filesystem, project and git tools whose implementation already
  exists in ``tools/`` and is not copied into Python;
* **user-configured third-party servers** from ``servers.toml``, named explicitly.

Neither is attached raw. Each gets its own gateway process
(:mod:`odessy.tools.upstream_gateway`), which mirrors the server's roster and puts Odessy's
policy, consent and audit in front of every call. This module only decides **whether** a
gateway may be attached and **how** it is launched; the gateway decides what each call may do.

The activation rule is a confinement rule, not a convenience flag
------------------------------------------------------------------

An upstream tool's effects happen *inside the server process*. Odessy can decide and ask
questions about a call, but it cannot apply its providers to that process the way
``terminal.run`` confines a command. So a session that asks for a restriction this host cannot
hold the server to must not get a server that ignores it:

* either upstream is refused whenever isolation is ``required``: no OS boundary is
  applied to that server process, regardless of a different host provider's capabilities;
* under ``preferred`` or ``not-required``, the explicit opt-in may attach it with
  ``process_confinement: none`` and every requested unfulfilled dimension recorded.

The activation record names the refusal; HarnessConfig.validate also raises before spawning
any process. A direct gateway launch independently rejects required isolation.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from odessy.runtime_backends.harness.sandbox import SandboxPlan
from odessy.tools.external_tools import ServerConfig

__all__ = [
    "EXTERNAL_PREFIX",
    "GATEWAY_MODULE",
    "NODE_SERVER_NAME",
    "NodeTools",
    "activation",
    "entries",
    "requested_dimensions",
]

GATEWAY_MODULE = "odessy.tools.upstream_gateway"
#: The MCP server name the runtime sees for the Node tool server.
NODE_SERVER_NAME = "odessy_node"
#: Third-party servers are ``odessy_ext_<configured name>``.
EXTERNAL_PREFIX = "odessy_ext_"

NODE_KIND = "node"
EXTERNAL_KIND = "external"


@dataclass(frozen=True)
class NodeTools:
    """The reviewed Node tool server: its interpreter and its built entry point."""

    executable: str
    entry: Path

    def describe(self) -> dict[str, Any]:
        return {"executable": self.executable, "entry": str(self.entry)}


def requested_dimensions(plan: SandboxPlan) -> tuple[str, ...]:
    """The dimensions this session asks to be enforced, in the plan's own vocabulary."""

    if plan.mode == "danger-full-access":
        return ()
    names: list[str] = []
    if plan.network == "deny":
        names.append("network")
    if plan.hidden_paths:
        names.append("hidden_paths")
    return tuple(names)


def activation(kind: str, plan: SandboxPlan) -> dict[str, Any]:
    """Whether an upstream of ``kind`` may be attached, and what stays unenforced.

    Host support for another command does not establish an upstream process boundary.
    Required isolation therefore always refuses until this path actually applies a
    verified provider. Preferred isolation records the exact weaker boundary explicitly.
    """

    requested = requested_dimensions(plan)
    # No OS confinement is applied to these upstream processes, even if a host
    # provider could confine a different CommandBridge operation.
    gaps = requested
    dimension = ", ".join(gaps)
    if plan.isolation == "required":
        return {
            "allowed": False,
            "unenforced": list(dict.fromkeys(("mode", *gaps))),
            "process_confinement": "none",
            "reason": (
                f"required upstream isolation is unavailable: no OS confinement is applied "
                f"to the {kind} server process (mode{', ' + dimension if dimension else ''}); "
                "it is not activated"
            ),
        }
    record: dict[str, Any] = {
        "allowed": True,
        "unenforced": list(gaps),
        # Said plainly: the server process runs without an OS sandbox of its own. What
        # constrains a call is the policy, consent and audit in front of it.
        "process_confinement": "none",
    }
    if gaps:
        record["note"] = (
            f"this host cannot enforce {dimension} for the {kind} server process; the call is "
            "decided by Odessy's policy and consent, not by that process boundary"
        )
    return record


def _common_arguments(*, plan: SandboxPlan, control: tuple[int, str] | None) -> list[str]:
    arguments = [
        "--workspace", plan.workspace_root,
        "--mode", plan.mode,
        "--isolation", plan.isolation,
        "--network", plan.network,
    ]
    if control is not None:
        arguments += ["--control-port", str(control[0])]
    for hidden in plan.hidden_paths:
        arguments += ["--hidden", hidden]
    for extra in plan.extra_writable:
        arguments += ["--extra-writable", extra]
    return arguments


def _environment(base: Mapping[str, str], control: tuple[int, str] | None,
                 names: Iterable[str] = (), source: Mapping[str, str] | None = None
                 ) -> list[dict[str, str]]:
    """The gateway's environment: platform names, the control token, and a server's own."""

    env = [{"name": name, "value": value} for name, value in base.items()]
    if control is not None:
        env.append({"name": "ODESSY_TOOL_CONTROL_TOKEN", "value": control[1]})
    source = source or {}
    for name in names:
        # Values are never copied into a diagnostic record; they are only passed on.
        if name in source:
            env.append({"name": name, "value": source[name]})
    return env


def _gateway(*, label: str, arguments: Sequence[str], base_env: Mapping[str, str],
             control: tuple[int, str] | None, tools_python: str,
             names: Iterable[str] = (), source: Mapping[str, str] | None = None
             ) -> dict[str, object]:
    return {
        "name": label,
        "command": tools_python or sys.executable,
        "args": ["-m", GATEWAY_MODULE, *arguments],
        "env": _environment(base_env, control, names, source),
    }


def entries(*, plan: SandboxPlan, control: tuple[int, str] | None, tools_python: str,
            node: NodeTools | None, externals: Sequence[ServerConfig],
            servers_file: Path | None, base_env: Mapping[str, str],
            source: Mapping[str, str] | None = None
            ) -> tuple[list[dict[str, object]], dict[str, Any]]:
    """The ``mcpServers`` entries to attach, plus the record of every decision made.

    A refused upstream is *absent* from the first list and *present* in the record with its
    reason — the caller turns that into diagnostics, never into a silent omission.
    """

    servers: list[dict[str, object]] = []
    record: dict[str, Any] = {"node": {"enabled": node is not None}, "external": {}}

    if node is not None:
        decision = activation(NODE_KIND, plan)
        record["node"].update(decision)
        record["node"].update(node.describe())
        if decision["allowed"]:
            arguments = ["--kind", NODE_KIND, "--label", NODE_SERVER_NAME]
            arguments += ["--node-exe", node.executable, "--entry", str(node.entry)]
            arguments += _common_arguments(plan=plan, control=control)
            servers.append(_gateway(
                label=NODE_SERVER_NAME, arguments=arguments, base_env=base_env,
                control=control, tools_python=tools_python))

    for config in externals:
        label = f"{EXTERNAL_PREFIX}{config.name}"
        decision = activation(EXTERNAL_KIND, plan)
        item = {"label": label, "trust": config.trust, "transport": config.transport}
        item.update(decision)
        record["external"][config.name] = item
        if not decision["allowed"]:
            continue
        arguments = ["--kind", EXTERNAL_KIND, "--label", label, "--server", config.name]
        if servers_file is not None:
            arguments += ["--servers-file", str(servers_file)]
        arguments += _common_arguments(plan=plan, control=control)
        # Only what this server declared: its own `env` names and the variables its
        # `headers` read from. A credential therefore reaches the gateway only when the
        # user asked for that server to have it.
        declared = (*config.env, *config.headers.values())
        servers.append(_gateway(
            label=label, arguments=arguments, base_env=base_env, control=control,
            tools_python=tools_python, names=declared, source=source))

    return servers, record
