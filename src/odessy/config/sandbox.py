"""Sandbox settings (``sandbox.toml``): how strictly commands are confined, and how.

::

    isolation = "auto"                # auto | required | preferred | not-required
    escalation = "ask"                # ask | never | workspace-write-only
    extra_writable = ["~/.cache/pip", "~/.npm"]
    hidden_paths = ["~/work/other-client"]   # added to the built-in list
    default_hidden = true

    [network]
    read_only = "deny"                # a command run without asking has no network
    workspace_write = "allow"         # one you confirmed keeps it, unless you say deny

    [limits]                          # 0 turns a limit off
    memory_mb = 4096
    processes = 256
    file_size_mb = 4096
    cpu_seconds = 120

    [targets.laptop]                  # the paired laptop's commands
    isolation = "preferred"
    escalation = "workspace-write-only"

The file lives with the user's config (``~/.config/odessy/sandbox.toml``, or
``ODESSY_SANDBOX_FILE``), never in the workspace, and the agent may not change it
(it is a protected path, like ``hooks.toml``). It is read once per process: an edit
takes effect when Odessy restarts. ``ODESSY_ISOLATION`` still wins over
``isolation`` when it is set.
"""

from __future__ import annotations

import functools
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from odessy.execution.limits import DEFAULT_LIMITS, ResourceLimits
from odessy.execution.roots import FORBIDDEN_WRITABLE, canonical_path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib

SANDBOX_FILE_ENV = "ODESSY_SANDBOX_FILE"

IsolationSetting = Literal["auto", "required", "preferred", "not-required"]
EscalationSetting = Literal["ask", "never", "workspace-write-only"]
ISOLATION_SETTINGS: tuple[str, ...] = ("auto", "required", "preferred", "not-required")
ESCALATION_SETTINGS: tuple[str, ...] = ("ask", "never", "workspace-write-only")
TARGETS = ("local", "laptop")

#: Folders and files no confined command needs to read: credentials, keys, browser
#: profiles, and Odessy's own configuration. ``~`` is the executing host's home.
#: A path that does not exist on that host is skipped there.
DEFAULT_HIDDEN_PATHS: tuple[str, ...] = (
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.azure",
    "~/.kube",
    "~/.docker",
    "~/.netrc",
    "~/.pypirc",
    "~/.npmrc",
    "~/.git-credentials",
    "~/.config/gcloud",
    "~/.config/gh",
    "~/.config/odessy",
    "~/.local/share/keyrings",
    "~/.password-store",
    "~/.mozilla",
    "~/.config/google-chrome",
    "~/.config/chromium",
    "~/.config/BraveSoftware",
    "~/.config/microsoft-edge",
    "~/Library/Keychains",
    "~/Library/Application Support/Google/Chrome",
    "~/Library/Application Support/Firefox",
    "~/AppData/Roaming/Microsoft/Credentials",
    "~/AppData/Local/Microsoft/Credentials",
    "~/AppData/Roaming/Microsoft/Protect",
    "~/AppData/Local/Google/Chrome/User Data",
    "~/AppData/Local/Microsoft/Edge/User Data",
    "~/AppData/Roaming/Mozilla/Firefox",
)


class SandboxConfigError(ValueError):
    """``sandbox.toml`` is malformed. The message names the file and the key."""


@dataclass(frozen=True)
class TargetSettings:
    isolation: IsolationSetting | None = None
    escalation: EscalationSetting | None = None


@dataclass(frozen=True)
class SandboxSettings:
    isolation: IsolationSetting | None = None
    escalation: EscalationSetting = "ask"
    network_read_only: Literal["allow", "deny"] = "deny"
    network_workspace_write: Literal["allow", "deny"] = "allow"
    hidden_paths: tuple[str, ...] = DEFAULT_HIDDEN_PATHS
    extra_writable: tuple[str, ...] = ()
    limits: ResourceLimits | None = DEFAULT_LIMITS
    targets: Mapping[str, TargetSettings] = field(default_factory=dict)
    source: Path | None = None

    def escalation_for(self, target: str) -> EscalationSetting:
        override = self.targets.get(target)
        return override.escalation if override is not None and override.escalation else self.escalation

    def isolation_setting_for(self, target: str) -> IsolationSetting | None:
        override = self.targets.get(target)
        return override.isolation if override is not None and override.isolation else None

    def network_for(self, mode: str) -> Literal["allow", "deny"]:
        if mode == "read-only":
            return self.network_read_only
        if mode == "workspace-write":
            return self.network_workspace_write
        return "allow"


DEFAULT_SETTINGS = SandboxSettings()


def sandbox_file(environ: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    explicit = source.get(SANDBOX_FILE_ENV, "").strip()
    return Path(explicit).expanduser() if explicit else Path.home() / ".config" / "odessy" / "sandbox.toml"


def _choice(where: str, key: str, value: Any, choices: tuple[str, ...]) -> Any:
    if value not in choices:
        raise SandboxConfigError(f"{where}: {key} must be one of {list(choices)} (got {value!r})")
    return value


def _paths(where: str, key: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise SandboxConfigError(f"{where}: {key} must be a list of paths")
    for item in value:
        if not (item.startswith("~/") or item == "~" or os.path.isabs(item) or Path(item).is_absolute()):
            raise SandboxConfigError(f"{where}: {key} entries must be absolute or start with ~/ (got {item!r})")
    return tuple(value)


def _normal(path: str) -> str:
    """The real location, so that a link to a hidden folder is seen for what it is."""

    return os.path.normcase(os.path.normpath(canonical_path(os.path.expanduser(path)))).rstrip("\\/")


def _check_writable(where: str, writable: tuple[str, ...], hidden: tuple[str, ...]) -> None:
    forbidden = {_normal(path) for path in FORBIDDEN_WRITABLE}
    hidden_normal = [_normal(path) for path in hidden]
    for item in writable:
        normal = _normal(item)
        if normal in forbidden or os.path.dirname(normal) == normal:
            raise SandboxConfigError(f"{where}: extra_writable cannot include {item!r}")
        for secret in hidden_normal:
            if normal == secret or normal.startswith(secret + os.sep) or secret.startswith(normal + os.sep):
                raise SandboxConfigError(f"{where}: extra_writable {item!r} overlaps the hidden path {secret!r}")


def _limits(where: str, value: Any) -> ResourceLimits | None:
    if not isinstance(value, dict):
        raise SandboxConfigError(f"{where}: [limits] must be a table")
    unknown = sorted(set(value) - {"memory_mb", "processes", "file_size_mb", "cpu_seconds"})
    if unknown:
        raise SandboxConfigError(f"{where}: unknown keys in [limits]: {unknown}")
    fields: dict[str, int | None] = {}
    for key, default in vars(DEFAULT_LIMITS).items():
        raw = value.get(key, default if default is not None else 0)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise SandboxConfigError(f"{where}: limits.{key} must be a whole number, 0 to turn it off")
        fields[key] = raw or None
    limits = ResourceLimits(**fields)
    return limits if any(vars(limits).values()) else None


def parse_sandbox(path: Path, data: Mapping[str, Any]) -> SandboxSettings:
    where = str(path)
    unknown = sorted(set(data) - {"isolation", "escalation", "extra_writable", "hidden_paths", "default_hidden",
                                  "network", "limits", "targets"})
    if unknown:
        raise SandboxConfigError(f"{where}: unknown keys {unknown}")
    isolation = _choice(where, "isolation", data["isolation"], ISOLATION_SETTINGS) if "isolation" in data else None
    escalation = _choice(where, "escalation", data.get("escalation", "ask"), ESCALATION_SETTINGS)
    default_hidden = data.get("default_hidden", True)
    if not isinstance(default_hidden, bool):
        raise SandboxConfigError(f"{where}: default_hidden must be true or false")
    hidden = (DEFAULT_HIDDEN_PATHS if default_hidden else ()) + _paths(where, "hidden_paths", data.get("hidden_paths", []))
    if len(hidden) > 64:
        raise SandboxConfigError(f"{where}: at most 64 hidden paths, the built-in list included")
    writable = _paths(where, "extra_writable", data.get("extra_writable", []))
    if len(writable) > 16:
        raise SandboxConfigError(f"{where}: at most 16 extra_writable folders")
    _check_writable(where, writable, hidden)
    network = data.get("network", {})
    if not isinstance(network, dict) or set(network) - {"read_only", "workspace_write"}:
        raise SandboxConfigError(f"{where}: [network] takes read_only and workspace_write")
    targets: dict[str, TargetSettings] = {}
    raw_targets = data.get("targets", {})
    if not isinstance(raw_targets, dict) or set(raw_targets) - set(TARGETS):
        raise SandboxConfigError(f"{where}: [targets] takes {list(TARGETS)}")
    for name, entry in raw_targets.items():
        if not isinstance(entry, dict) or set(entry) - {"isolation", "escalation"}:
            raise SandboxConfigError(f"{where}: [targets.{name}] takes isolation and escalation")
        targets[name] = TargetSettings(
            isolation=_choice(where, f"targets.{name}.isolation", entry["isolation"], ISOLATION_SETTINGS) if "isolation" in entry else None,
            escalation=_choice(where, f"targets.{name}.escalation", entry["escalation"], ESCALATION_SETTINGS) if "escalation" in entry else None,
        )
    return SandboxSettings(
        isolation=isolation,
        escalation=escalation,
        network_read_only=_choice(where, "network.read_only", network.get("read_only", "deny"), ("allow", "deny")),
        network_workspace_write=_choice(where, "network.workspace_write", network.get("workspace_write", "allow"), ("allow", "deny")),
        hidden_paths=hidden,
        extra_writable=writable,
        limits=_limits(where, data["limits"]) if "limits" in data else DEFAULT_LIMITS,
        targets=targets,
        source=path,
    )


def read_sandbox_settings(path: Path) -> SandboxSettings:
    """Parse ``path`` (uncached; the doctor uses this). Missing file: the defaults."""

    if not path.is_file():
        return DEFAULT_SETTINGS
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SandboxConfigError(f"{path}: cannot read sandbox file: {error}") from error
    return parse_sandbox(path, data)


@functools.lru_cache(maxsize=4)
def _cached(key: str) -> SandboxSettings:
    return read_sandbox_settings(Path(key))


def load_sandbox_settings(environ: Mapping[str, str] | None = None) -> SandboxSettings:
    """The settings for this process, read once: an edit applies after a restart,
    so a file changed while Odessy runs cannot loosen the turn in progress."""

    return _cached(str(sandbox_file(environ)))


def reset_sandbox_cache() -> None:
    _cached.cache_clear()


__all__ = [
    "DEFAULT_HIDDEN_PATHS",
    "DEFAULT_SETTINGS",
    "SANDBOX_FILE_ENV",
    "SandboxConfigError",
    "SandboxSettings",
    "TargetSettings",
    "load_sandbox_settings",
    "parse_sandbox",
    "read_sandbox_settings",
    "reset_sandbox_cache",
    "sandbox_file",
]
