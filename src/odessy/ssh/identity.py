"""OpenSSH identity and command construction for the SSH connection method.

This is the production half of prompt 17's client-side spike, not a second SSH
implementation: it asks the installed ``ssh`` what an alias means, reads the host keys
the user already trusts, and builds the same non-interactive argv every call uses.
"""

from __future__ import annotations

import base64
import glob
import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

SSH_OPTIONS: tuple[str, ...] = (
    "-T",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "ForwardAgent=no",
    "-o", "ClearAllForwardings=yes",
    "-o", "ControlMaster=no",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=2",
    "-o", "LogLevel=ERROR",
)

_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class SshError(ValueError):
    """A host cannot be resolved, identified or used as configured."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class SshHost:
    alias: str
    hostname: str
    user: str
    port: int = 22
    identity_files: tuple[str, ...] = ()
    proxy_jump: str | None = None
    config_path: str | None = None
    known_host_files: tuple[str, ...] = ()

    @property
    def endpoint(self) -> str:
        if self.port == 22:
            return self.hostname
        host = f"[{self.hostname}]" if ":" in self.hostname else self.hostname
        return f"{host}:{self.port}"


@dataclass(frozen=True)
class SshIdentity:
    host: SshHost
    fingerprints: tuple[tuple[str, str], ...] = ()
    version: str = ""

    @property
    def primary_fingerprint(self) -> str:
        return self.fingerprints[0][1] if self.fingerprints else ""

    @property
    def known(self) -> bool:
        return bool(self.fingerprints)


def resolve_host(alias: str, *, ssh: str = "ssh", config_path: str | None = None,
                 timeout: float = 10) -> SshHost:
    """Resolve an alias with the installed OpenSSH client; no connection is made."""

    _validate_alias(alias)
    args = [ssh, "-G"]
    if config_path:
        args += ["-F", config_path]
    args.append(alias)
    try:
        completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise SshError("SSH_NOT_AVAILABLE", f"cannot run the SSH client: {error}") from error
    if completed.returncode != 0:
        raise SshError("SSH_CONFIG_INVALID",
                       (completed.stderr or completed.stdout or "ssh -G failed").strip()[:400])
    values: dict[str, list[str]] = {}
    for line in completed.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key and value:
            values.setdefault(key, []).append(value.strip())
    hostname = _last(values, "hostname") or alias
    user = _last(values, "user")
    if not user:
        raise SshError("SSH_CONFIG_INVALID", f"{alias!r} resolves without a user")
    try:
        port = int(_last(values, "port") or "22")
    except ValueError as error:
        raise SshError("SSH_CONFIG_INVALID", f"{alias!r} resolves to an invalid port") from error
    if not 1 <= port <= 65535:
        raise SshError("SSH_CONFIG_INVALID", f"{alias!r} resolves to an invalid port")
    jump = _last(values, "proxyjump")
    known = tuple(_expand(path) for line in values.get("userknownhostsfile", ())
                  for path in line.split())
    return SshHost(alias=alias, hostname=hostname, user=user, port=port,
                   identity_files=tuple(values.get("identityfile", ())),
                   proxy_jump=None if jump in (None, "", "none") else jump,
                   config_path=config_path, known_host_files=known)


def read_identity(host: SshHost, *, ssh_keygen: str = "ssh-keygen",
                  ssh: str = "ssh", timeout: float = 10) -> SshIdentity:
    """Read the trusted host keys for this endpoint. Unknown means refusal."""

    candidates = [host.hostname]
    if host.port != 22:
        candidates.append(f"[{host.hostname}]:{host.port}")
    files = host.known_host_files or (str(Path.home() / ".ssh" / "known_hosts"),)
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for path in files:
        for name in candidates:
            args = [ssh_keygen, "-l", "-F", name, "-f", path]
            try:
                completed = subprocess.run(args, capture_output=True, text=True,
                                           encoding="utf-8", errors="replace",
                                           timeout=timeout, check=False)
            except (OSError, subprocess.SubprocessError) as error:
                raise SshError("SSH_NOT_AVAILABLE", f"cannot run ssh-keygen: {error}") from error
            if completed.returncode != 0:
                continue
            for line in completed.stdout.splitlines():
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 3 or not parts[2].startswith("SHA256:"):
                    continue
                marker = (parts[1].lower(), parts[2])
                if marker not in seen:
                    seen.add(marker)
                    found.append(marker)
    if not found:
        raise SshError(
            "SSH_HOST_KEY_UNKNOWN",
            f"the host key for {host.hostname} is not in known_hosts; add it out of band, "
            "never by accepting an automatic prompt",
        )
    return SshIdentity(host=host, fingerprints=tuple(found), version=_ssh_version(ssh, timeout))


def ssh_argv(host: SshHost, remote_command: str, *, ssh: str = "ssh") -> tuple[str, ...]:
    if not remote_command or "\x00" in remote_command or "\n" in remote_command:
        raise SshError("SSH_INVALID_COMMAND", "the remote command must be one non-empty line")
    _validate_alias(host.alias)
    config = ("-F", host.config_path) if host.config_path else ()
    return (ssh, *config, *SSH_OPTIONS, host.alias, remote_command)


def _ssh_version(ssh: str, timeout: float) -> str:
    try:
        completed = subprocess.run([ssh, "-V"], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=timeout,
                                   check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (completed.stderr or completed.stdout or "").strip().splitlines()
    return text[0][:200] if text else ""


def _validate_alias(alias: str) -> None:
    if not _ALIAS.fullmatch(alias):
        raise SshError(
            "SSH_ALIAS_INVALID",
            "host alias must be 1-64 letters, digits, '.', '_' or '-' and not start with '-'",
        )


def _expand(path: str) -> str:
    return os.path.expanduser(path)


def _last(values: dict[str, list[str]], key: str) -> str | None:
    found = values.get(key)
    return found[-1] if found else None


def list_aliases(config_path: str | Path | None = None) -> tuple[str, ...]:
    """Concrete host aliases from the user's OpenSSH config, without values."""

    path = Path(config_path) if config_path is not None else Path.home() / ".ssh" / "config"
    aliases: list[str] = []
    seen: set[str] = set()
    for name in _read_host_names(path, depth=0, visited=set()):
        if name not in seen:
            seen.add(name)
            aliases.append(name)
    return tuple(aliases)


def _read_host_names(path: Path, *, depth: int, visited: set[Path]):
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


def discovered_keys(host: SshHost, *, ssh_keyscan: str = "ssh-keyscan",
                    timeout: float = 6) -> tuple[tuple[str, str], ...]:
    """Unauthenticated keys advertised by the host, for review only."""

    args = [ssh_keyscan, "-T", str(max(1, int(timeout))), "-p", str(host.port), host.hostname]
    try:
        completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise SshError("SSH_KEYSCAN_UNAVAILABLE", f"cannot discover host keys: {error}") from error
    if completed.returncode != 0:
        raise SshError("SSH_KEYSCAN_FAILED",
                       (completed.stderr or "ssh-keyscan failed").strip()[:400])
    keys: list[tuple[str, str]] = []
    for line in completed.stdout.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        key_type, encoded = parts[1], parts[2]
        try:
            blob = base64.b64decode(encoded, validate=True)
        except Exception:
            continue
        digest = base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
        keys.append((key_type.lower(), "SHA256:" + digest))
    return tuple(keys)


def inspect_alias(alias: str, *, ssh: str = "ssh", ssh_keygen: str = "ssh-keygen",
                  ssh_keyscan: str = "ssh-keyscan",
                  config_path: str | None = None) -> dict[str, object]:
    """Resolve identity and compare trusted vs advertised keys; no trust write."""

    host = resolve_host(alias, ssh=ssh, config_path=config_path)
    known: tuple[tuple[str, str], ...] = ()
    known_error = ""
    try:
        known = read_identity(host, ssh_keygen=ssh_keygen, ssh=ssh).fingerprints
    except SshError as error:
        if error.code != "SSH_HOST_KEY_UNKNOWN":
            known_error = str(error)
    discovered: tuple[tuple[str, str], ...] = ()
    discover_error = ""
    try:
        discovered = discovered_keys(host, ssh_keyscan=ssh_keyscan)
    except SshError as error:
        discover_error = str(error)
    known_set = {fingerprint for _, fingerprint in known}
    discovered_set = {fingerprint for _, fingerprint in discovered}
    return {
        "alias": alias,
        "hostname": host.hostname,
        "user": host.user,
        "port": host.port,
        "endpoint": host.endpoint,
        "known": bool(known),
        "known_fingerprints": [value for _, value in known],
        "discovered_fingerprints": [value for _, value in discovered],
        "changed": bool(known and discovered and not (known_set & discovered_set)),
        "discovery": "ssh-keyscan (unauthenticated)" if discovered else "",
        "known_error": known_error,
        "discover_error": discover_error,
    }


def probe_connection(host: SshHost, *, ssh: str = "ssh", timeout: float = 8) -> dict[str, object]:
    """One bounded, read-only login. It does not read or write trust files."""

    import time

    started = time.monotonic()
    try:
        completed = subprocess.run(ssh_argv(host, "printf odessy-connect-ok", ssh=ssh),
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "SSH_TIMEOUT", "message": f"no answer within {timeout:.0f} s",
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}
    elapsed = round((time.monotonic() - started) * 1000, 1)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "SSH authentication failed").strip()
        return {"ok": False, "code": "SSH_AUTH_FAILED", "message": message[:400],
                "elapsed_ms": elapsed}
    if "odessy-connect-ok" not in completed.stdout:
        return {"ok": False, "code": "SSH_UNEXPECTED_OUTPUT",
                "message": "SSH returned unexpected output", "elapsed_ms": elapsed}
    return {"ok": True, "code": "SSH_OK", "message": "", "elapsed_ms": elapsed}



__all__ = [
    "SSH_OPTIONS",
    "SshError",
    "SshHost",
    "SshIdentity",
    "discovered_keys",
    "inspect_alias",
    "list_aliases",
    "probe_connection",
    "read_identity",
    "resolve_host",
    "ssh_argv",
]
