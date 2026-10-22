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

import json
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
    """A configured peer. ``alias`` is the enrolled identity in the Rust registry.

    ``enrolled_fingerprint`` and ``grant_revision`` come from `odessy-link peers
    list --json` at startup. They are not secrets: the fingerprint is the value the
    operator already compared, and the revision is a counter. Both travel into
    execution-target metadata so an approval names the certificate that will be
    trusted and the local permission state it was granted under.
    """

    alias: str
    binary: Path
    enrolled_fingerprint: str | None = None
    grant_revision: int | None = None

    @property
    def prefix(self) -> str:
        """Tool namespace. Tools surface as ``<alias>.<tool>``."""
        return self.alias

    @property
    def command_tool(self) -> str:
        return f"{self.prefix}.terminal.run"

    def server_config(self) -> ServerConfig:
        metadata = {"transport": "odessy-link", "paired_alias": self.alias}
        if self.enrolled_fingerprint:
            metadata["paired_fingerprint_sha256"] = self.enrolled_fingerprint
        if self.grant_revision is not None:
            metadata["paired_grant_revision"] = str(self.grant_revision)
        return ServerConfig(
            name=self.prefix,
            transport="stdio",
            command=(str(self.binary), "connect", "--peer", self.alias),
            # Only what `odessy-link` needs to locate its own state and Node: not the
            # ambient environment, and nothing the model can influence.
            env=("ODESSY_LINK_STATE_DIR", "ODESSY_LINK_NODE"),
            target_metadata=metadata,
        )

    def server(self, sessions=None) -> "PairedServer":
        return PairedServer(self.server_config(), sessions)


@dataclass(frozen=True)
class PairedServer:
    """The peer's MCP server, offered under the names the peer actually uses.

    The remote Node server already names its command tool (``laptop.terminal.run`` in
    split mode), so adding an alias prefix here produced ``jetson.laptop.terminal.run``
    and no tool the policy or the model contract knew. The prefix is therefore empty:
    tools pass through unchanged and are checked against the same contracts.
    """

    config: ServerConfig
    sessions: object = None

    @property
    def prefix(self) -> str:
        return ""

    def _inner(self):
        # Imported here so a test can substitute the session-backed server.
        from odessy.tools.external_tools import NamespacedServer

        return NamespacedServer(self.config, self.sessions)

    async def list_tools(self):
        return await self._inner().list_tools()

    async def call_tool(self, remote_name, arguments, *, before_dispatch=None):
        return await self._inner().call_tool(
            remote_name, arguments, before_dispatch=before_dispatch
        )

    def session(self):
        return self._inner().session()


def configured_alias(env: Mapping[str, str] | None = None) -> str:
    source = os.environ if env is None else env
    return source.get(PAIRED_HOST_ENV, "").strip()


def paired_host(env: Mapping[str, str] | None = None, *, prober=None) -> PairedHost | None:
    """The configured paired host, or ``None`` when none is configured.

    Raises `PairedConfigError` when the configuration cannot be honoured, so a
    half-set environment fails loudly instead of silently using another transport.
    A failing probe is not fatal here: `connect` reports the real error, and the
    metadata simply carries no fingerprint until enrolment is readable.
    """

    validate_configuration(env)
    alias = configured_alias(env)
    if not alias:
        return None
    binary = link_binary(env)
    host = PairedHost(alias=alias, binary=binary)
    details: dict[str, object] = {}
    try:
        details = (prober or probe)(host)
    except Exception:  # noqa: BLE001 - the transport reports the real failure
        details = {}
    fingerprint = details.get("fingerprint_sha256")
    revision = details.get("grant_revision")
    return PairedHost(
        alias=alias,
        binary=binary,
        enrolled_fingerprint=fingerprint if isinstance(fingerprint, str) else None,
        grant_revision=revision if isinstance(revision, int) else None,
    )


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
    """Report whether the binary answers and what it has enrolled for this alias.

    Uses `peers list --json`, which reads the local registry only: no connection is
    attempted, and the fingerprint returned is the public certificate digest the
    operator compared, never key material.
    """

    result: dict[str, object] = {"alias": host.alias, "binary": str(host.binary)}
    try:
        completed = subprocess.run(
            [str(host.binary), "peers", "list", "--json"],
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
    try:
        listing = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as error:
        result.update(ok=False, detail=f"peers list returned unreadable JSON: {error}")
        return result
    peers = listing.get("peers") or []
    enrolled = next((peer for peer in peers if peer.get("alias") == host.alias), None)
    result.update(
        ok=True,
        enrolled_peers=len(peers),
        grant_revision=listing.get("grant_revision"),
        configured_alias_present=enrolled is not None,
    )
    if enrolled is not None:
        result["fingerprint_sha256"] = enrolled.get("fingerprint_sha256")
        result["capability_mcp"] = bool(enrolled.get("capability_mcp"))
        result["workspace"] = enrolled.get("workspace")
    return result
