"""Paired-host transport: ``odessy-link connect`` is this deployment's split target.

The pairing plan keeps ``odessy-link`` a thin transport and admission layer. This
module is the Python half: it decides *whether* a paired host is configured, builds
the stdio MCP client that runs ``odessy-link connect``, and refuses configurations
that contradict it. It never falls back to a legacy remote URL: a paired host that
cannot be reached is an error, not a reason to use another transport.

Certificate identity is enforced by ``odessy-link`` itself; nothing here re-derives
or weakens it. What this module carries into Odessy is the enrolled alias, so
execution-target metadata and approval bindings name the peer that was admitted.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from odessy.tools.external_tools import ServerConfig

PAIRED_HOST_ENV = "ODESSY_PAIRED_HOST"
LINK_BIN_ENV = "ODESSY_LINK_BIN"
#: Seconds allowed for a status probe. Not the transport's timeout: that one is the
#: MCP server's own.
PROBE_SECONDS = 10
_ALIAS = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class PairedConfigError(ValueError):
    """The paired-host configuration is incomplete or contradicts another setting."""


@dataclass(frozen=True)
class PairedHost:
    """A configured peer. ``alias`` is the enrolled identity in the Rust registry."""

    alias: str
    binary: Path

    @property
    def prefix(self) -> str:
        """Tool namespace. Tools surface as ``<alias>.<tool>``."""
        return self.alias

    @property
    def command_tool(self) -> str:
        return f"{self.prefix}.terminal.run"

    def server_config(self) -> ServerConfig:
        return ServerConfig(
            name=self.prefix,
            transport="stdio",
            command=(str(self.binary), "connect", "--peer", self.alias),
            # Only what `odessy-link` needs to locate its own state and Node: not the
            # ambient environment, and nothing the model can influence.
            env=("ODESSY_LINK_STATE_DIR", "ODESSY_LINK_NODE"),
            target_metadata={
                "transport": "odessy-link",
                "paired_alias": self.alias,
            },
        )


def configured_alias(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    return source.get(PAIRED_HOST_ENV, "").strip()


def paired_host(env: Mapping[str, str] | None = None) -> PairedHost | None:
    """The configured paired host, or ``None`` when none is configured.

    Raises `PairedConfigError` when the configuration cannot be honoured, so a
    half-set environment fails loudly instead of silently using another transport.
    """

    validate_configuration(env)
    alias = configured_alias(env)
    if not alias:
        return None
    return PairedHost(alias=alias, binary=link_binary(env))


def link_binary(env: Mapping[str, str] | None = None) -> Path:
    """Locate ``odessy-link``: explicit path, then PATH, then this checkout's build.

    The last candidate keeps a fresh clone usable without installing anything,
    but only when the binary was actually built; otherwise the error names both the
    setting and the command that produces it.
    """

    source = os.environ if env is None else env
    explicit = source.get(LINK_BIN_ENV, "").strip()
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise PairedConfigError(
                f"{LINK_BIN_ENV} points at {path}, which is not a file"
            )
        return path
    found = shutil.which("odessy-link")
    if found:
        return Path(found)
    repo = Path(__file__).resolve().parents[3]
    for profile in ("release", "debug"):
        for name in ("odessy-link.exe", "odessy-link"):
            candidate = repo / "native" / "odessy-link" / "target" / profile / name
            if candidate.is_file():
                return candidate
    raise PairedConfigError(
        f"{PAIRED_HOST_ENV} is set but no odessy-link binary was found. Build it with "
        "`cargo build --release` in native/odessy-link, put odessy-link on PATH, or "
        f"set {LINK_BIN_ENV} to its path."
    )


def validate_configuration(env: Mapping[str, str] | None = None) -> None:
    """Reject settings that contradict paired mode.

    Checked before any connection is attempted, so a contradictory environment is a
    configuration error rather than a confusing runtime failure.
    """

    from odessy.config.deployment import DEPLOYMENT_ENV, REMOTE_URL_ENVS

    source = os.environ if env is None else env
    alias = configured_alias(source)
    explicit_binary = source.get(LINK_BIN_ENV, "").strip()
    if not alias:
        if explicit_binary:
            raise PairedConfigError(
                f"{LINK_BIN_ENV} is set without {PAIRED_HOST_ENV}; name the peer alias first"
            )
        return
    if not _ALIAS.match(alias):
        raise PairedConfigError(
            f"{PAIRED_HOST_ENV} must be 1-64 ASCII letters, digits, '-' or '_' (got {alias!r})"
        )
    declared = source.get(DEPLOYMENT_ENV, "").strip().lower()
    if declared and declared != "split":
        raise PairedConfigError(
            f"{DEPLOYMENT_ENV}={declared} contradicts {PAIRED_HOST_ENV}={alias}: "
            "a paired host uses split execution"
        )
    urls = [name for name in REMOTE_URL_ENVS if source.get(name, "").strip()]
    if urls:
        raise PairedConfigError(
            f"{PAIRED_HOST_ENV} cannot be combined with {', '.join(urls)}; "
            "a paired host replaces the raw remote MCP URL and never falls back to it"
        )


def probe(host: PairedHost, *, timeout: float = PROBE_SECONDS) -> dict[str, object]:
    """Report whether the binary answers and which peers it has enrolled.

    Only ``peers list`` is used, which reads the local registry: no connection is
    attempted and no key material is printed. A paired host that is configured but
    has no enrolment cannot admit anything, so that is reported as a problem.
    """

    result: dict[str, object] = {"alias": host.alias, "binary": str(host.binary)}
    try:
        completed = subprocess.run(
            [str(host.binary), "peers", "list"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        result.update(ok=False, detail=f"cannot run {host.binary}: {error}")
        return result
    if completed.returncode != 0:
        result.update(
            ok=False,
            detail=(completed.stderr or completed.stdout or "peers list failed").strip(),
        )
        return result
    text = completed.stdout
    result["configured_alias_present"] = f"\n{host.alias} " in f"\n{text}"
    result["enrolled_peers"] = len([line for line in text.splitlines() if line and not line.startswith(" ")])
    result["ok"] = True
    return result
