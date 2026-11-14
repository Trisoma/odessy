"""Prompt 17 spike: an SSH connection method for one existing computer.

This module is a bounded feasibility probe, not the production transport (that is
prompt 18). It answers the questions the product can actually answer on one Windows
laptop, without a model, a remote machine or a network:

* which aliases already exist in the user's OpenSSH configuration;
* what the alias resolves to, and which host key the user already trusts for it;
* what a safe, non-interactive SSH command looks like for that alias;
* how a fixed, read-only status sample would be collected and parsed.

It deliberately does **not** authenticate a host, connect anywhere, accept a host key,
modify ``sshd``/``authorized_keys``, or run a model. A successful SSH login proves only
transport reachability; monitoring and execution are separate capabilities.

The chosen reuse tier is the existing MCP-over-stdio client: the remote side runs the
same ``odessy.tools.stdio_server`` that the Harness path already uses, and this module
only builds the ``ssh`` command that carries its stdin/stdout. Nothing about the tool
surface, authority or confinement is re-implemented here.
"""

from __future__ import annotations

import glob
import math
import os
import re
import shlex
import shutil
import subprocess
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Options that make the connection non-interactive and refuse to widen trust.
#: ``BatchMode`` turns a password/passphrase prompt into an error, so the command
#: cannot hang waiting for a person; only keys/agent/config are reused.
SSH_OPTIONS: tuple[str, ...] = (
    "-T",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "UpdateHostKeys=no",
    "-o", "ForwardAgent=no",
    "-o", "ClearAllForwardings=yes",
    "-o", "ControlMaster=no",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=2",
    "-o", "LogLevel=ERROR",
)

#: The remote host must already have an Odessy source or wheel and its Python
#: dependencies. Odessy never installs, builds or provisions the remote.
REMOTE_TOOL_SERVER = "odessy.tools.stdio_server"

DEFAULT_SSH_TIMEOUT = 15.0
DEFAULT_STATUS_TIMEOUT = 6.0
MAX_STATUS_BYTES = 16 * 1024

_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SERVER_SLUG = re.compile(r"[^a-z0-9_-]+")
_REMOTE_EXECUTABLE = re.compile(r"^/[A-Za-z0-9_./-]+$")
_POSIX_WORKSPACE = re.compile(r"^/[^\x00\r\n]*$")


class SshConfigError(ValueError):
    """The alias or configuration cannot be used as written."""


@dataclass(frozen=True)
class HostKey:
    key_type: str
    fingerprint: str


@dataclass(frozen=True)
class SshAlias:
    """One concrete ``Host`` entry after OpenSSH resolved its effective values."""

    alias: str
    hostname: str
    user: str
    port: int = 22
    identity_files: tuple[str, ...] = ()
    proxy_jump: str | None = None
    #: The config file the alias came from, when it is not OpenSSH's default.
    config_path: str | None = None

    @property
    def endpoint(self) -> str:
        if self.port == 22:
            return self.hostname
        host = f"[{self.hostname}]" if ":" in self.hostname else self.hostname
        return f"{host}:{self.port}"


@dataclass(frozen=True)
class HostIdentity:
    """The host key material already trusted for one endpoint.

    This is transport identity, not a person, and not a capability grant. It is
    read from ``known_hosts`` only; nothing here accepts an unknown key.
    """

    alias: str
    hostname: str
    port: int
    keys: tuple[HostKey, ...] = ()

    @property
    def known(self) -> bool:
        return bool(self.keys)

    @property
    def primary(self) -> str:
        return self.keys[0].fingerprint if self.keys else ""


@dataclass(frozen=True)
class Capabilities:
    """What one computer has actually been shown able to do, separately.

    Every field starts false. A successful SSH login proves transport reachability
    and nothing else: it does not enable monitoring, execution or a workspace.
    """

    transport: bool = False
    monitoring: bool = False
    execution: bool = False
    workspace: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "transport": self.transport,
            "monitoring": self.monitoring,
            "execution": self.execution,
            "workspace": self.workspace,
        }


@dataclass(frozen=True)
class Computer:
    """A product-level computer associated with this SSH alias.

    The existing product association is the in-memory execution target
    (``tools/lan_executor.py`` / ``ServerConfig.target_metadata``), not a second
    registry. This record is the minimum extra shape a UI needs: identity, method,
    workspace and separately proven capabilities.
    """

    alias: SshAlias
    identity: HostIdentity
    display_name: str
    method: str = "ssh"
    workspace: str | None = None
    capabilities: Capabilities = field(default_factory=Capabilities)

    def shown(self) -> dict[str, object]:
        return {
            "display_name": self.display_name,
            "method": self.method,
            "alias": self.alias.alias,
            "endpoint": self.alias.endpoint,
            "host_key": self.identity.primary,
            "known_host": self.identity.known,
            "workspace": self.workspace,
            "capabilities": self.capabilities.as_dict(),
        }


@dataclass(frozen=True)
class TransportProbe:
    ok: bool
    code: str
    detail: str
    elapsed_ms: float

    def shown(self) -> dict[str, object]:
        return {"ok": self.ok, "code": self.code, "detail": self.detail,
                "elapsed_ms": round(self.elapsed_ms, 1)}


@dataclass(frozen=True)
class StatusSample:
    observed_at: str
    source: str
    host_key: str
    load_1m: float
    load_5m: float
    load_15m: float
    disk_used_percent: int

    @property
    def freshness_seconds(self) -> float:
        try:
            observed = datetime.fromisoformat(self.observed_at)
        except ValueError:
            return math.inf
        return max(0.0, (datetime.now(timezone.utc) - observed).total_seconds())

    def shown(self) -> dict[str, object]:
        return {
            "observed_at": self.observed_at,
            "source": self.source,
            "host_key": self.host_key,
            "load_1m": self.load_1m,
            "load_5m": self.load_5m,
            "load_15m": self.load_15m,
            "disk_used_percent": self.disk_used_percent,
        }


@dataclass(frozen=True)
class Unavailable:
    """An explicit failure. There is no zero-filled sample and no guessed value."""

    code: str
    detail: str

    def shown(self) -> dict[str, object]:
        return {"unavailable": True, "code": self.code, "detail": self.detail}


# --- aliases and identity -----------------------------------------------------------


def list_aliases(config_path: str | Path | None = None) -> tuple[str, ...]:
    """Concrete host aliases in the user's OpenSSH configuration, in file order.

    ``Host`` lines may list several patterns; wildcard and negated patterns are not
    concrete computers and are skipped. ``Include`` is followed (bounded) so a
    configuration split across files behaves like the one OpenSSH reads.
    """

    path = Path(config_path) if config_path is not None else Path.home() / ".ssh" / "config"
    aliases: list[str] = []
    seen: set[str] = set()
    for name in _read_host_names(path, depth=0, visited=set()):
        if name not in seen:
            seen.add(name)
            aliases.append(name)
    return tuple(aliases)


def _read_host_names(path: Path, *, depth: int, visited: set[Path]) -> Iterable[str]:
    if depth > 3 or len(visited) >= 16:
        return
    try:
        resolved = path.resolve()
    except OSError:
        return
    if resolved in visited or not resolved.is_file():
        return
    visited.add(resolved)
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        keyword = parts[0].lower()
        if keyword == "include" and len(parts) > 1:
            for pattern in parts[1:]:
                candidate = Path(os.path.expanduser(pattern))
                if not candidate.is_absolute():
                    candidate = resolved.parent / candidate
                for match in glob.glob(str(candidate)):
                    yield from _read_host_names(Path(match), depth=depth + 1, visited=visited)
            continue
        if keyword != "host" or len(parts) < 2:
            continue
        for token in parts[1:]:
            if not token or token.startswith("!") or any(ch in token for ch in "*?["):
                continue
            if _ALIAS.fullmatch(token):
                yield token


def resolve_alias(alias: str, *, ssh: str = "ssh",
                  config_path: str | Path | None = None) -> SshAlias:
    """Ask the installed OpenSSH client what *alias* means. No connection is made.

    ``ssh -G`` prints the effective configuration after all files, defaults and
    expansions; it is the same resolution a real connection would use.
    """

    _validate_alias(alias)
    args = [ssh, "-G"]
    if config_path is not None:
        args += ["-F", str(config_path)]
    args.append(alias)
    completed = _run(args, timeout=10)
    if completed.returncode != 0:
        raise SshConfigError(
            f"cannot resolve {alias!r}: {(completed.stderr or completed.stdout or '').strip()}"
        )
    values: dict[str, list[str]] = {}
    for line in completed.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key and value:
            values.setdefault(key, []).append(value.strip())
    hostname = _first(values, "hostname") or alias
    user = _first(values, "user")
    if not user:
        raise SshConfigError(f"{alias!r} resolves without a user")
    port_text = _first(values, "port") or "22"
    try:
        port = int(port_text)
    except ValueError as error:
        raise SshConfigError(f"{alias!r} resolves to an invalid port: {port_text!r}") from error
    if not 1 <= port <= 65535:
        raise SshConfigError(f"{alias!r} resolves to an invalid port: {port!r}")
    jump = _first(values, "proxyjump")
    return SshAlias(
        alias=alias,
        hostname=hostname,
        user=user,
        port=port,
        identity_files=tuple(values.get("identityfile", ())),
        proxy_jump=None if jump in (None, "", "none") else jump,
        config_path=str(config_path) if config_path is not None else None,
    )


def host_identity(computer: SshAlias, *, ssh_keygen: str = "ssh-keygen",
                  known_hosts: str | Path | None = None) -> HostIdentity:
    """The host keys already present in ``known_hosts`` for this endpoint.

    An empty result means the host is unknown: the caller must refuse to connect,
    not accept the key automatically. That refusal is the point, so this function
    never writes, adds or trusts anything.
    """

    candidates = [computer.hostname]
    if computer.port != 22:
        candidates.append(f"[{computer.hostname}]:{computer.port}")
    keys: list[HostKey] = []
    seen: set[tuple[str, str]] = set()
    for name in candidates:
        args = [ssh_keygen, "-l", "-F", name]
        if known_hosts is not None:
            args += ["-f", str(known_hosts)]
        completed = _run(args, timeout=10)
        if completed.returncode != 0:
            continue
        for line in completed.stdout.splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            key_type, fingerprint = parts[1].lower(), parts[2]
            if not fingerprint.startswith("SHA256:"):
                continue
            marker = (key_type, fingerprint)
            if marker not in seen:
                seen.add(marker)
                keys.append(HostKey(key_type, fingerprint))
    return HostIdentity(alias=computer.alias, hostname=computer.hostname,
                        port=computer.port, keys=tuple(keys))


def describe(alias: str, *, ssh: str = "ssh", ssh_keygen: str = "ssh-keygen",
             config_path: str | Path | None = None,
             known_hosts: str | Path | None = None) -> Computer:
    """Resolve one alias and read its trusted host keys. Still no connection."""

    resolved = resolve_alias(alias, ssh=ssh, config_path=config_path)
    identity = host_identity(resolved, ssh_keygen=ssh_keygen, known_hosts=known_hosts)
    return Computer(alias=resolved, identity=identity, display_name=alias)


# --- transport ------------------------------------------------------------------------


def ssh_argv(computer: SshAlias, remote_command: str, *, ssh: str = "ssh") -> tuple[str, ...]:
    """The argv that runs one remote command over the configured alias."""

    if not remote_command or "\x00" in remote_command or "\n" in remote_command:
        raise SshConfigError("the remote command must be one non-empty line")
    _validate_alias(computer.alias)
    config = ("-F", computer.config_path) if computer.config_path else ()
    return (ssh, *config, *SSH_OPTIONS, computer.alias, remote_command)


def remote_stdio_command(remote_python: str, workspace: str, *, mode: str = "read-only",
                         isolation: str = "preferred", network: str = "deny") -> str:
    """The POSIX shell command that starts the existing Odessy stdio server remotely.

    The remote runs the same reviewed tool server as the Harness path; this module
    adds no tool, no policy and no sandbox. ``mode``, ``isolation`` and ``network``
    are fixed at launch and are not tool arguments.
    """

    if not _REMOTE_EXECUTABLE.fullmatch(remote_python):
        raise SshConfigError(
            "the remote Python must be one absolute POSIX path without shell metacharacters"
        )
    if not workspace or not _POSIX_WORKSPACE.fullmatch(workspace):
        raise SshConfigError("the remote workspace must be an absolute POSIX path")
    if mode not in ("read-only", "workspace-write"):
        raise SshConfigError(f"unsupported remote mode: {mode!r}")
    if isolation not in ("preferred", "required", "not-required", "auto"):
        raise SshConfigError(f"unsupported remote isolation: {isolation!r}")
    if network not in ("allow", "deny"):
        raise SshConfigError(f"unsupported remote network policy: {network!r}")
    return " ".join((
        shlex.quote(remote_python),
        "-m", REMOTE_TOOL_SERVER,
        "--workspace", shlex.quote(workspace),
        "--mode", mode,
        "--isolation", isolation,
        "--network", network,
    ))


def server_config(computer: Computer, remote_python: str, workspace: str, *,
                  mode: str = "read-only", isolation: str = "preferred",
                  network: str = "deny", timeout: int = 30,
                  ssh: str = "ssh") -> Any:
    """An existing ``ServerConfig`` for this computer, carried as stdio over SSH.

    ``env`` stays empty: ssh authenticates with the user's own configuration, agent
    and keys, and no credential or token is put in argv or the child environment.
    """

    from odessy.tools.external_tools import ServerConfig

    remote = remote_stdio_command(remote_python, workspace, mode=mode,
                                  isolation=isolation, network=network)
    command = ssh_argv(computer.alias, remote, ssh=ssh)
    metadata = {
        "transport": "ssh",
        "ssh_alias": computer.alias.alias,
        "ssh_endpoint": computer.alias.endpoint,
        "workspace": workspace,
    }
    if computer.identity.known:
        metadata["ssh_host_key_fingerprint"] = computer.identity.primary
    return ServerConfig(
        name=_server_name(computer.alias.alias),
        transport="stdio",
        command=command,
        env=(),
        timeout=timeout,
        target_metadata=metadata,
    )


def probe_transport(computer: SshAlias, *, ssh: str = "ssh",
                    timeout: float = DEFAULT_SSH_TIMEOUT) -> TransportProbe:
    """One bounded SSH login that runs ``printf`` and nothing else.

    A success proves the alias, key/agent, known-host entry and remote shell all
    work. It still proves none of the monitoring, execution or workspace grants.
    """

    started = time.perf_counter()
    try:
        completed = _run(ssh_argv(computer, "printf odessy-ssh-ok", ssh=ssh),
                         timeout=timeout)
    except subprocess.TimeoutExpired:
        return TransportProbe(False, "SSH_TIMEOUT",
                              f"no answer within {timeout:.0f} s",
                              (time.perf_counter() - started) * 1000)
    elapsed = (time.perf_counter() - started) * 1000
    if completed.returncode != 0:
        return TransportProbe(False, "SSH_AUTH_FAILED",
                              _clean(completed.stderr or completed.stdout, 400), elapsed)
    if "odessy-ssh-ok" not in completed.stdout:
        return TransportProbe(False, "SSH_UNEXPECTED_OUTPUT",
                              _clean(completed.stdout, 200), elapsed)
    return TransportProbe(True, "SSH_OK", "", elapsed)


# --- monitoring -----------------------------------------------------------------------


def monitoring_command() -> str:
    """The one fixed read-only command the collector runs. No model text enters it."""

    return "cat /proc/loadavg; printf '\\n---\\n'; df -P -k /"


def collect_status(computer: Computer, *, ssh: str = "ssh",
                   timeout: float = DEFAULT_STATUS_TIMEOUT) -> StatusSample | Unavailable:
    """One bounded status sample, or an explicit unavailable result.

    Sampling is on demand only: no daemon, no polling loop and no process left
    behind. The fixed command reads load averages and the root filesystem usage.
    """

    try:
        completed = _run(ssh_argv(computer.alias, monitoring_command(), ssh=ssh),
                         timeout=timeout)
    except subprocess.TimeoutExpired:
        return Unavailable("SSH_TIMEOUT", f"status probe exceeded {timeout:.0f} s")
    if completed.returncode != 0:
        return Unavailable("SSH_COMMAND_FAILED", _clean(completed.stderr or completed.stdout, 400))
    raw = completed.stdout
    if len(raw.encode("utf-8", "replace")) > MAX_STATUS_BYTES:
        return Unavailable("SSH_OUTPUT_LIMIT", f"status output above {MAX_STATUS_BYTES} bytes")
    return parse_status(raw, host_key=computer.identity.primary)


def parse_status(raw: str, *, host_key: str = "") -> StatusSample | Unavailable:
    """Parse the fixed command's output; unknown or partial output is unavailable."""

    load_text, separator, disk_text = raw.partition("---")
    if not separator:
        return Unavailable("SSH_PARSE_FAILED", "status output has no section separator")
    load_fields = load_text.split()
    if len(load_fields) < 3:
        return Unavailable("SSH_PARSE_FAILED", "load average is missing")
    try:
        load = tuple(float(value) for value in load_fields[:3])
    except ValueError:
        return Unavailable("SSH_PARSE_FAILED", "load average is not numeric")
    if any(not math.isfinite(value) or value < 0 for value in load):
        return Unavailable("SSH_PARSE_FAILED", "load average is outside a usable range")
    disk_line = _last_data_line(disk_text)
    if disk_line is None:
        return Unavailable("SSH_PARSE_FAILED", "disk usage is missing")
    fields = disk_line.split()
    if len(fields) < 4 or not fields[-2].endswith("%"):
        return Unavailable("SSH_PARSE_FAILED", "disk usage has no capacity field")
    try:
        disk = int(fields[-2][:-1])
    except ValueError:
        return Unavailable("SSH_PARSE_FAILED", "disk capacity is not an integer")
    if not 0 <= disk <= 100:
        return Unavailable("SSH_PARSE_FAILED", "disk capacity is outside 0..100")
    return StatusSample(
        observed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source="ssh",
        host_key=host_key,
        load_1m=load[0],
        load_5m=load[1],
        load_15m=load[2],
        disk_used_percent=disk,
    )


def _last_data_line(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    # ``df -P`` prints a header; take the last row (the root filesystem).
    for line in reversed(lines[1:] if len(lines) > 1 else lines):
        if line.split() and not line.lower().startswith("filesystem"):
            return line
    return None


# --- capability matrix ----------------------------------------------------------------


def capabilities(*, transport: bool, monitoring_enabled: bool, monitoring_ok: bool,
                 execution_enabled: bool, tools_listed: bool,
                 workspace_ok: bool) -> Capabilities:
    """Derive the matrix from separate evidence, never from one success.

    Monitoring needs its own opt-in *and* a successful sample; execution needs its
    own grant *and* a successful tool listing; the workspace needs the remote to
    accept it. A successful login sets none of them.
    """

    return Capabilities(
        transport=transport,
        monitoring=bool(monitoring_enabled and monitoring_ok),
        execution=bool(execution_enabled and tools_listed),
        workspace=bool(workspace_ok),
    )


# --- internals ------------------------------------------------------------------------


def _validate_alias(alias: str) -> None:
    if not _ALIAS.fullmatch(alias):
        raise SshConfigError(
            f"host alias must be 1-64 letters, digits, '.', '_' or '-' and not start with '-': {alias!r}"
        )


def _server_name(alias: str) -> str:
    slug = _SERVER_SLUG.sub("-", alias.lower()).strip("-") or "host"
    return ("ssh-" + slug)[:32].rstrip("-")


def _first(values: Mapping[str, Sequence[str]], key: str) -> str | None:
    found = values.get(key)
    return found[-1] if found else None


def _run(args: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _clean(text: str, limit: int) -> str:
    value = " ".join((text or "").split())
    return value[:limit]


def default_ssh() -> str:
    return shutil.which("ssh") or "ssh"


def default_ssh_keygen() -> str:
    return shutil.which("ssh-keygen") or "ssh-keygen"


__all__ = [
    "Capabilities",
    "Computer",
    "DEFAULT_SSH_TIMEOUT",
    "DEFAULT_STATUS_TIMEOUT",
    "HostIdentity",
    "HostKey",
    "MAX_STATUS_BYTES",
    "REMOTE_TOOL_SERVER",
    "SSH_OPTIONS",
    "SshAlias",
    "SshConfigError",
    "StatusSample",
    "TransportProbe",
    "Unavailable",
    "capabilities",
    "collect_status",
    "default_ssh",
    "default_ssh_keygen",
    "describe",
    "host_identity",
    "list_aliases",
    "monitoring_command",
    "parse_status",
    "probe_transport",
    "remote_stdio_command",
    "resolve_alias",
    "server_config",
    "ssh_argv",
]
