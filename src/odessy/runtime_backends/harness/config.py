"""Everything the Harness process is allowed to see, resolved once and validated.

There is no discovery and no fallback. ``ODESSY_HARNESS_EXE`` names exactly one
executable; if it is absent the Harness engine is *unavailable* and session creation
fails, rather than quietly running the legacy loop or picking up some other binary.

The child environment is an allowlist, not a copy of the parent: a minimal set of
platform variables, one explicit Harness home, and whatever the operator listed in
``ODESSY_HARNESS_PASS_ENV``. Provider credentials are *not* forwarded at this stage
(prompt 04 owns model configuration).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from odessy.runtime_backends.contracts import RuntimeUnavailable
from odessy.runtime_backends.harness.pin import PINNED_PROFILE, PINNED_VERSION
from odessy.runtime_backends.harness.profile import (
    NARROW_PATCH_NAME,
    materialize_narrow_patch,
)

ENGINE = "harness"

DEFAULT_STARTUP_TIMEOUT = 20.0
DEFAULT_TURN_TIMEOUT = 300.0
DEFAULT_SHUTDOWN_TIMEOUT = 10.0
DEFAULT_DIAGNOSTIC_LINES = 200

#: Inherited so the runtime can start and find its own temporary directory. Nothing
#: here identifies a user session, a project or a credential.
_POSIX_BASE_ENV = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SHELL")
_WINDOWS_BASE_ENV = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "OS",
)


def _base_env_names() -> tuple[str, ...]:
    return _WINDOWS_BASE_ENV if sys.platform == "win32" else _POSIX_BASE_ENV


#: What a child needs to bind or connect a socket, beyond what it needs to start. On
#: Windows a missing ``SystemRoot`` fails ``socket()`` with WSAEPROVIDERFAILEDINIT; the
#: packaged bridge plugin listens, so this would read as "live text is unavailable"
#: without saying why.
_NETWORK_ENV = ("SYSTEMROOT",) if sys.platform == "win32" else ()


def default_home(environ: Mapping[str, str] | None = None) -> Path:
    """The per-user Harness home Odessy owns when the operator does not name one."""
    environ = os.environ if environ is None else environ
    if sys.platform == "win32":
        root = environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = str(Path.home() / "Library" / "Application Support")
    else:
        root = environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(root) / "Odessy" / "harness" / "home"


#: Row ids the packaged overlay disables. An extra overlay that names one of these (or any
#: ``tool-`` / ``mcp-`` row, or ``plan-mode``) is refused: patch layers apply in argv
#: order, so a later layer could otherwise re-enable a tool that prompt 03 must not expose.
PROTECTED_ROWS = frozenset({
    "tool-pwsh", "tool-bash", "tool-fs", "tool-fs-search", "tool-web", "tool-skill",
    "tool-subagent", "tool-subagent-control", "tool-subagent-list-agents",
    "tool-subagent-fork", "tool-workflow", "tool-todo", "tool-goal", "tool-jobs",
    "tool-working-directory", "plan-mode", "mcp-resources",
})
_PROTECTED_PREFIXES = ("tool-", "mcp-", "subagent", "plan-mode")
_ROW_ID = re.compile(r"^\s*-?\s*id:\s*['\"]?([A-Za-z0-9_.:/-]+)", re.MULTILINE)


def protected_rows_named(path: Path) -> list[str]:
    """Row ids in an overlay that this stage must not let anyone change."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    named = []
    for match in _ROW_ID.finditer(text):
        row = match.group(1)
        if row in PROTECTED_ROWS or row.startswith(_PROTECTED_PREFIXES):
            named.append(row)
    return sorted(set(named))


def assert_extras_leave_tools_alone(extras: Iterable[Path]) -> None:
    """Refuse an extra overlay that would take part in the tool surface.

    The packaged overlay is applied **last** (see :meth:`HarnessConfig.argv`), so it wins
    any same-row conflict; this check closes the other door, an overlay that names a row
    the packaged one does not (a new tool row, or a differently-spelled one). Together they
    mean "no model-facing tool" is a property of the launch, not of runtime detection after
    a tool has already run.
    """
    for extra in extras:
        named = protected_rows_named(extra)
        if named:
            raise RuntimeUnavailable(
                ENGINE, "an extra profile overlay may not modify a tool row",
                detail=f"{extra} names {', '.join(named)}",
            )


@dataclass(frozen=True)
class HarnessConfig:
    """A fully resolved launch description. Validated before the first spawn."""

    command: tuple[str, ...]
    home: Path
    workspace: Path
    profile: str = PINNED_PROFILE
    #: Extra overlays. They are applied *before* ``narrow_patch`` and may not name a tool
    #: row (:func:`assert_extras_leave_tools_alone`), so they cannot widen the surface.
    patches: tuple[Path, ...] = ()
    #: The selected model route, as the one-row ``llm-pi-ai`` overlay for this session.
    #: Emitted after extras and before the narrow overlay: it configures a provider, never
    #: a tool row.
    route_patch: Path | None = None
    #: The reviewed narrow overlay. Its position is a field, not a convention: it is always
    #: emitted last, so no extra overlay can re-enable what it disabled.
    narrow_patch: Path | None = None
    #: Variables the session adds to the child's allowlist (a credential for the selected
    #: route). Values are never logged; :meth:`describe` reports names only.
    extra_env: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)
    expected_version: str = PINNED_VERSION
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT
    turn_timeout: float = DEFAULT_TURN_TIMEOUT
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT
    diagnostic_lines: int = DEFAULT_DIAGNOSTIC_LINES
    passthrough: tuple[str, ...] = field(default_factory=tuple)
    #: Whether ``--version`` identity must also match the reviewed artifact digest.
    #: Production resolutions keep this ``True``; only a config constructed in code (tests,
    #: a script-driven fake runtime) may turn it off, and the environment cannot.
    verify_artifact: bool = True
    #: Where the passthrough values are read from. Recorded so that a config resolved
    #: from an explicit mapping also *launches* with that mapping, instead of silently
    #: falling back to the process environment.
    source_env: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)

    def argv(self) -> list[str]:
        argv = [*self.command, "--profile", self.profile]
        for patch in self.ordered_patches():
            argv += ["--patch", str(patch)]
        return argv

    def ordered_patches(self) -> list[Path]:
        """Extras, then the model route, then the narrow overlay last.

        The narrow overlay stays last so no extra overlay can re-enable a tool row; the
        route overlay only names the provider row (``llm-pi-ai``) and therefore never
        competes with it.
        """
        ordered = [p for p in self.patches if p not in (self.narrow_patch, self.route_patch)]
        if self.route_patch is not None:
            ordered.append(self.route_patch)
        if self.narrow_patch is not None:
            ordered.append(self.narrow_patch)
        return ordered

    def child_env(self, parent: Mapping[str, str] | None = None) -> dict[str, str]:
        if parent is None:
            parent = self.source_env or os.environ
        env = {name: parent[name] for name in _base_env_names() if name in parent}
        for name in self.passthrough:
            if name in parent:
                env[name] = parent[name]
        for name, value in self.extra_env.items():
            env[name] = value
        env["DSH_HOME"] = str(self.home)
        return env

    def missing_for_network(self, parent: Mapping[str, str] | None = None) -> tuple[str, ...]:
        """Which inherited names the child lacks to open the live-text socket.

        Winsock initialisation reads ``SystemRoot``; without it ``socket()`` fails with
        ``WSAEPROVIDERFAILEDINIT``, which surfaces only as "no bridge listening". An
        explicit (partial) ``source_env`` is a legitimate way to launch hermetically, so
        this is reported rather than silently repaired from the process environment.
        """
        env = self.child_env(parent)
        return tuple(name for name in _NETWORK_ENV if name not in env)

    def describe(self) -> dict[str, object]:
        """Diagnostic summary. Deliberately omits the environment."""
        return {
            "command": list(self.command),
            "profile": self.profile,
            "patches": [str(p) for p in self.ordered_patches()],
            "home": str(self.home),
            "route_patch": str(self.route_patch) if self.route_patch else "",
            "narrow_patch": str(self.narrow_patch) if self.narrow_patch else "",
            # Names only: a credential value never enters a diagnostic record.
            "extra_env": sorted(self.extra_env),
            "workspace": str(self.workspace),
            "expected_version": self.expected_version,
            "startup_timeout": self.startup_timeout,
            "turn_timeout": self.turn_timeout,
        }

    def validate(self) -> None:
        if not self.command:
            raise RuntimeUnavailable(ENGINE, "no runtime executable is configured")
        executable = Path(self.command[0])
        if not executable.is_absolute():
            raise RuntimeUnavailable(
                ENGINE, "the runtime executable must be an absolute path",
                detail=str(executable),
            )
        if not executable.is_file():
            raise RuntimeUnavailable(
                ENGINE, "the configured executable does not exist", detail=str(executable),
            )
        if self.narrow_patch is None:
            raise RuntimeUnavailable(ENGINE, "no Odessy narrow overlay is configured")
        if Path(self.narrow_patch).name != NARROW_PATCH_NAME:
            raise RuntimeUnavailable(
                ENGINE, "the narrow overlay must be the reviewed one",
                detail=str(self.narrow_patch),
            )
        for patch in self.ordered_patches():
            if not patch.is_file():
                raise RuntimeUnavailable(
                    ENGINE, "a configured profile overlay is missing", detail=str(patch),
                )
        if not self.workspace.is_dir():
            raise RuntimeUnavailable(
                ENGINE, "the configured workspace directory does not exist",
                detail=str(self.workspace),
            )
        if not self.home.is_absolute():
            raise RuntimeUnavailable(
                ENGINE, "the Harness home must be an absolute path", detail=str(self.home),
            )
        if self.startup_timeout <= 0 or self.turn_timeout <= 0 or self.shutdown_timeout <= 0:
            raise RuntimeUnavailable(ENGINE, "timeouts must be positive")


def _split_paths(value: str) -> tuple[Path, ...]:
    return tuple(Path(part.strip()).expanduser()
                 for part in value.split(os.pathsep) if part.strip())


def _split_names(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.replace(os.pathsep, ",").split(",")
                 if part.strip())


def _seconds(environ: Mapping[str, str], name: str, default: float) -> float:
    raw = (environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeUnavailable(ENGINE, f"{name} must be a number of seconds",
                                 detail=raw) from exc
    if value <= 0:
        raise RuntimeUnavailable(ENGINE, f"{name} must be positive", detail=raw)
    return value


def from_environment(
    environ: Mapping[str, str] | None = None,
    *,
    workspace: str | os.PathLike[str] | None = None,
) -> HarnessConfig:
    """Resolve the launch description from explicit configuration only."""
    environ = os.environ if environ is None else environ
    executable = (environ.get("ODESSY_HARNESS_EXE") or "").strip()
    if not executable:
        raise RuntimeUnavailable(
            ENGINE, "not configured: ODESSY_HARNESS_EXE is unset",
            detail="the Harness engine is developer-only; point ODESSY_HARNESS_EXE at "
                   "the pinned runtime executable",
        )
    home = Path((environ.get("ODESSY_HARNESS_HOME") or "").strip()
                or default_home(environ)).expanduser()
    workspace_path = Path(workspace if workspace is not None
                          else (environ.get("ODESSY_HARNESS_WORKSPACE") or "").strip()
                          or Path.cwd()).expanduser()
    # Extras are validated *before* they are combined with the packaged overlay, because
    # the packaged one legitimately names every tool row.
    extras = _split_paths(environ.get("ODESSY_HARNESS_PATCH") or "")
    assert_extras_leave_tools_alone(extras)
    config = HarnessConfig(
        command=(executable,),
        home=home,
        workspace=workspace_path,
        profile=(environ.get("ODESSY_HARNESS_PROFILE") or "").strip() or PINNED_PROFILE,
        patches=extras,
        narrow_patch=materialize_narrow_patch(home),
        expected_version=(environ.get("ODESSY_HARNESS_VERSION") or "").strip()
        or PINNED_VERSION,
        startup_timeout=_seconds(environ, "ODESSY_HARNESS_STARTUP_TIMEOUT",
                                 DEFAULT_STARTUP_TIMEOUT),
        turn_timeout=_seconds(environ, "ODESSY_HARNESS_TURN_TIMEOUT", DEFAULT_TURN_TIMEOUT),
        shutdown_timeout=_seconds(environ, "ODESSY_HARNESS_SHUTDOWN_TIMEOUT",
                                  DEFAULT_SHUTDOWN_TIMEOUT),
        passthrough=_split_names(environ.get("ODESSY_HARNESS_PASS_ENV") or ""),
        source_env=dict(environ),
    )
    config.validate()
    return config
