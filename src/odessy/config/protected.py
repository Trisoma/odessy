"""Odessy's own configuration files: one list for every guard that protects them.

The policy refuses a command or write that names these, and the config guard denies
every call once one of the guarded files changes. Each file can be moved with its own
environment variable, so a file is protected where it really is, not only when it
sits in ``~/.config/odessy`` next to the others.

* ``hooks.toml`` runs programs as the user, outside the sandbox.
* ``sandbox.toml`` and ``commands.toml`` decide what is confined and what is asked.
* ``servers.toml`` starts third-party servers as the user and can mark their tools
  ``allow``.
* the user's ``odessy.env`` (or ``ODESSY_CONFIG_FILE``) can point any of the above
  somewhere else on the next start.

The hooks file's own scripts are added by ``odessy.runtime.hooks.protected_paths``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def _unique(paths) -> tuple[Path, ...]:
    seen: dict[str, Path] = {}
    for path in paths:
        seen.setdefault(os.path.normcase(str(Path(path).expanduser())), Path(path))
    return tuple(seen.values())


def guarded_config_files(environ: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    """Files whose change mid-run denies every call (``ConfigGuard``).

    ``hooks.toml`` is not here: it has a fingerprint of its own, which also covers its
    scripts. ``odessy.env`` is not here either: the desktop settings write it while
    Odessy runs, so it is protected from the agent but may change.
    """

    from odessy.config.commands import commands_file
    from odessy.config.sandbox import sandbox_file
    from odessy.tools.external_tools import servers_file

    return _unique((sandbox_file(environ), commands_file(environ), servers_file(environ)))


def protected_config_paths(environ: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    """Every file and folder of Odessy's configuration the agent may not change."""

    from odessy.config.config_file import CONFIG_FILE_ENV, user_config_file
    from odessy.config.sandbox import sandbox_file
    from odessy.runtime.hooks import hooks_file

    source = os.environ if environ is None else environ
    # A path is protected whether or not the file exists, so creating one is refused
    # too. The hooks and sandbox folders are protected whole, as before: either may
    # hold files their settings name.
    hooks, sandbox = hooks_file(environ), sandbox_file(environ)
    paths = [hooks, hooks.parent, sandbox, sandbox.parent, *guarded_config_files(environ)]
    paths.append(user_config_file())
    explicit = source.get(CONFIG_FILE_ENV, "").strip()
    if explicit:
        paths.append(Path(explicit).expanduser())
    return _unique(paths)


__all__ = ["guarded_config_files", "protected_config_paths"]
