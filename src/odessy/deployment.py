"""Which machines Odessy runs on, and what the model is told about them.

Two deployments:

- ``single``: one computer runs everything. The Node tool server is started locally
  on loopback and executes commands on this computer as ``terminal.run``.
- ``split``: the original two-machine layout. This machine (the Jetson) runs the
  model and ``terminal.run``; a laptop runs the Node tool server, reached over the
  network, which executes commands there as ``laptop.terminal.run``.

The mode is read from ``ODESSY_DEPLOYMENT``. When it is unset, a configured remote
tool server URL means ``split`` (so an existing two-machine setup keeps working with
no new setting) and its absence means ``single``.
"""

from __future__ import annotations

import os
import platform
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal

DEPLOYMENT_ENV = "ODESSY_DEPLOYMENT"
#: Overrides for the operating system the model is told about. ``LOCAL`` is this
#: machine (detected when unset); ``LAPTOP`` is the remote laptop in ``split`` mode,
#: which cannot be detected from here and is left unstated when unset.
LOCAL_OS_ENV = "ODESSY_LOCAL_OS"
LAPTOP_OS_ENV = "ODESSY_LAPTOP_OS"
REMOTE_URL_ENVS = ("ODESSY_MCP_SERVER_URLS", "ODESSY_TOOL_SERVER_URL")

Deployment = Literal["single", "split"]
DEPLOYMENTS: tuple[Deployment, ...] = ("single", "split")

#: Tools that exist only when a second machine does. In ``single`` mode they are never
#: offered to the model: "the laptop" is this computer, and two command tools for one
#: machine only give a small model a wrong choice to make.
SPLIT_ONLY_TOOLS = frozenset({"laptop.terminal.run"})

_available_tools: ContextVar[frozenset[str] | None] = ContextVar("available_tools", default=None)


@contextmanager
def model_tool_scope(names: Iterable[str]) -> Iterator[None]:
    """Limit model-facing tools for this run without changing other tasks or runs."""
    token = _available_tools.set(frozenset(names))
    try:
        yield
    finally:
        _available_tools.reset(token)


def tool_is_available(name: str) -> bool:
    available = _available_tools.get()
    return available is None or name in available


_OS_ALIASES = {
    "windows": "windows",
    "win32": "windows",
    "linux": "linux",
    "darwin": "macos",
    "macos": "macos",
    "mac": "macos",
    "osx": "macos",
}
_OS_DESCRIPTIONS = {
    "windows": "Windows; commands run in Windows PowerShell",
    "linux": "Linux; commands run in sh",
    "macos": "macOS; commands run in sh",
}
#: A few idiomatic commands per shell. Told only "PowerShell", a 1.2B model still wrote
#: `df -h C` and `echo $PATH` for the Windows laptop; a handful of examples anchors it.
_SHELL_HINTS = {
    "windows": "PowerShell examples: Get-Process, Get-PSDrive, Get-ChildItem -Recurse, $env:PATH",
    "linux": "sh examples: ps, df -h, free -h, find . -name",
    "macos": "sh examples: ps, df -h, vm_stat, find . -name",
}


class DeploymentConfigError(ValueError):
    """Raised when ``ODESSY_DEPLOYMENT`` or an OS override has an unknown value."""


def remote_tool_server_urls(env: Mapping[str, str] | None = None) -> list[str]:
    """The remote MCP server URLs configured for ``split`` mode, in order."""

    source = os.environ if env is None else env
    raw = next((source[name] for name in REMOTE_URL_ENVS if source.get(name, "").strip()), "")
    return [url.strip() for url in raw.replace(";", ",").split(",") if url.strip()]


def deployment_mode(env: Mapping[str, str] | None = None) -> Deployment:
    source = os.environ if env is None else env
    raw = source.get(DEPLOYMENT_ENV, "").strip().lower()
    if not raw:
        return "split" if remote_tool_server_urls(source) else "single"
    if raw not in DEPLOYMENTS:
        raise DeploymentConfigError(
            f"{DEPLOYMENT_ENV} must be one of: {', '.join(DEPLOYMENTS)} (got {raw!r})"
        )
    return raw  # type: ignore[return-value]


def model_tool_names(mode: Deployment | None = None) -> list[str]:
    """The tools the model is offered in this deployment, in contract order."""

    # Imported here: tool_contracts imports this module for the same reason.
    from odessy.tool_contracts import TOOL_REQUEST_SCHEMAS

    resolved = mode or deployment_mode()
    return [
        name
        for name in TOOL_REQUEST_SCHEMAS
        if (resolved == "split" or name not in SPLIT_ONLY_TOOLS) and tool_is_available(name)
    ]


def command_tool_for_laptop(mode: Deployment | None = None) -> str:
    """The tool a request addressed to "my laptop" must use."""

    return "laptop.terminal.run" if (mode or deployment_mode()) == "split" else "terminal.run"


def _normalize_os(raw: str, env_name: str) -> str:
    key = raw.strip().lower()
    if key not in _OS_ALIASES:
        raise DeploymentConfigError(
            f"{env_name} must be one of: windows, linux, macos (got {raw!r})"
        )
    return _OS_ALIASES[key]


def local_os(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    override = source.get(LOCAL_OS_ENV, "").strip()
    if override:
        return _normalize_os(override, LOCAL_OS_ENV)
    return _OS_ALIASES.get(platform.system().lower(), "linux")


def environment_prompt(
    offered: Iterable[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Where the offered command tools run, in a sentence or two; empty if none is offered.

    Without this the model guesses the shell, and a PowerShell command sent to sh (or
    the reverse) fails for a reason the user cannot see. Only the tools in ``offered``
    (default: every tool this deployment offers) are named: naming a tool the model was
    not given invites it to call that tool anyway.
    """

    source = os.environ if env is None else env
    mode = deployment_mode(source)
    tools = set(offered) if offered is not None else set(model_tool_names(mode))
    local = local_os(source)
    here = f"{_OS_DESCRIPTIONS[local]}. {_SHELL_HINTS[local]}"
    if mode == "single":
        if "terminal.run" not in tools:
            return ""
        return (
            f"terminal.run runs commands on the user's own computer ({here}), "
            "which is also the laptop if the user mentions one."
        )
    parts = []
    if "terminal.run" in tools:
        parts.append(f"terminal.run runs commands on this machine ({here}).")
    if "laptop.terminal.run" in tools:
        laptop_raw = source.get(LAPTOP_OS_ENV, "").strip()
        laptop = ""
        if laptop_raw:
            laptop_os = _normalize_os(laptop_raw, LAPTOP_OS_ENV)
            laptop = f" ({_OS_DESCRIPTIONS[laptop_os]}. {_SHELL_HINTS[laptop_os]})"
        parts.append(
            f"laptop.terminal.run runs commands on the user's laptop, a separate machine{laptop}."
        )
    return " ".join(parts)
