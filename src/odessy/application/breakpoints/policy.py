"""Which tool calls are "critical steps" that get before/after breakpoints.

Two signals are combined, as agreed:

* a whitelist of tools with side effects (writes, process execution, remote
  commands, external/MCP tools) — these always get breakpoints;
* the policy decision's risk level — ``high``/``medium`` calls are promoted even
  if their name is not on the whitelist.

Read-only observation and pure computation stay non-critical so ordinary turns
do not fill the breakpoint stream. This module is a pure function layer: it
imports only the dependency-free tool catalog and reads attributes off the
decision duck-style, so it can be tested without a policy engine.
"""
from __future__ import annotations

from typing import Any, Mapping

from odessy.tools.catalog import PROCESS_TOOLS, READ_ONLY_TOOLS

#: Tools whose effect can change workspace files or the outside world.
#: External/MCP names are handled separately because they are user-configured.
SIDE_EFFECT_TOOLS = frozenset({
    'filesystem.write',
    'log.append',
    'ssh.run',
    *PROCESS_TOOLS,
})

#: Risk values that promote a call to critical regardless of its name.
PROMOTING_RISK = frozenset({'high', 'medium'})


def _is_external(name: str) -> bool:
    """External/MCP tool names carry a namespace separator, unlike builtins.

    The catalog's ``BUILTIN_TOOLS`` is the authoritative in-process set; anything
    dispatched that is not builtin and not a known read-only tool is treated as
    external and therefore potentially side-effecting.
    """
    from odessy.tools.catalog import BUILTIN_TOOLS
    if name in BUILTIN_TOOLS:
        return False
    return True


def is_critical(name: Any, arguments: Any, decision: Any = None, *,
                external: bool | None = None) -> bool:
    """Whether a tool call should get a before/after execution breakpoint.

    ``decision`` may be a ``PolicyDecision`` (has ``.risk``) or a mapping with a
    ``risk`` key. ``arguments`` is accepted for call-site symmetry and future
    path-sensitive rules; the current policy is name- and risk-based only.

    ``external`` can be passed by the caller that already knows the tool is an
    external/MCP tool, avoiding a second catalog import in hot paths.
    """
    if not isinstance(name, str) or not name:
        return False
    if name in SIDE_EFFECT_TOOLS:
        return True
    is_ext = _is_external(name) if external is None else external
    if is_ext:
        return True
    risk = None
    if decision is not None:
        risk = getattr(decision, 'risk', None)
        if risk is None and isinstance(decision, Mapping):
            risk = decision.get('risk')
    return isinstance(risk, str) and risk.lower() in PROMOTING_RISK
