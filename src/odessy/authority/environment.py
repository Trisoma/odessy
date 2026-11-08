"""Which environment variables each kind of child process inherits: one place for all.

API keys and tokens live in the environment, so every child Odessy starts gets an
allowlist rather than a filtered copy. The lists differ on purpose, and the reason
for each difference is written here, next to the others, so they cannot drift apart
unnoticed:

* a sandboxed command gets the least: no home folder, so a tool looking for its
  credentials there finds nothing, plus the PowerShell module variables it needs to
  start quickly;
* a hook runs as the user and may need the home and app-data folders;
* a coding CLI keeps its own login in its home and config folders, and may be given
  the one credential variable its profile names;
* a third-party server gets what it needs to start, and the variables the user
  listed for it in ``servers.toml``.

Names are compared in upper case, as Windows does.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

#: Enough to find and start a program on any of the three systems.
_BASE = frozenset({"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "LANG", "LC_ALL"})

#: A command run in the sandbox (``terminal.run``).
SANDBOX_COMMAND_ENV = _BASE | {
    "TMPDIR",
    "TERM",
    # Preserve an explicitly configured module search path. Reconstructing it
    # in a stripped environment can trigger unrelated installed-module scans.
    "PSMODULEPATH",
    # Let the host choose a writable module-analysis cache. Cache reuse alone
    # does not bound module discovery; preserve PSMODULEPATH above as well.
    "PSMODULEANALYSISCACHEPATH",
}

#: A hook program from ``hooks.toml``.
HOOK_ENV = _BASE | {
    "SYSTEMDRIVE", "TMPDIR", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "APPDATA", "LOCALAPPDATA", "LC_CTYPE", "TZ",
}

#: A coding CLI (Codex, Claude Code, a custom agent) started for a job.
CODING_CLI_ENV = _BASE | {"HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"}

#: A stdio server from ``servers.toml``, before the variables its entry lists.
MCP_SERVER_ENV = frozenset({"PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG"})


def inherit(environ: Mapping[str, str], allowed: Iterable[str]) -> dict[str, str]:
    """The variables of ``environ`` whose names are in ``allowed`` (case-insensitive)."""

    names = {name.upper() for name in allowed}
    return {name: value for name, value in environ.items() if name.upper() in names}


__all__ = ["CODING_CLI_ENV", "HOOK_ENV", "MCP_SERVER_ENV", "SANDBOX_COMMAND_ENV", "inherit"]
