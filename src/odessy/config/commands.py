"""Commands you have approved in advance for one workspace (``commands.toml``).

``pytest`` or ``npm test`` runs dozens of times a day; asking each time teaches the
user to stop reading prompts. The file lists, per workspace, command prefixes that
run without asking::

    [[workspaces]]
    path = "D:/projects/odessy"
    commands = ["pytest", "npm test", "npm run build"]

The file lives with the user's config (``~/.config/odessy/commands.toml``, or
``ODESSY_COMMANDS_FILE``), never in the workspace: a repository must not be able to
approve its own commands.

A listed command skips the question, not the rules. It still runs in
``workspace-write`` mode (as a confirmed command would), and everything the policy
checks first still applies: the deny rules, the workspace boundary, the timeout limit,
and installers, which always need a typed ``yes``. A command matches an entry when its
words start with the entry's words and it has no chaining or redirection.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib

COMMANDS_FILE_ENV = "ODESSY_COMMANDS_FILE"

#: Characters that chain, redirect or substitute. An entry with one would approve more
#: than the command it names; a command with one never matches an entry.
SHELL_SYNTAX = re.compile(r"[;&|`\n(){}<>$%]")

#: Programs that run whatever they are given. Listed alone they would approve every
#: script, so an entry must say what they run ("python -m pytest", "npx vitest").
_TOO_BROAD = frozenset(
    {
        "python", "python3", "py", "node", "deno", "bun", "npx", "pnpx", "bash", "sh", "zsh",
        "pwsh", "powershell", "cmd", "cmd.exe", "ruby", "perl", "php", "java", "dotnet", "go",
        "cargo", "npm", "pnpm", "yarn", "uv", "make", "git",
    }
)


class CommandsConfigError(ValueError):
    """The commands file is malformed. Raised with the file and entry named."""


def default_commands_file() -> Path:
    return Path.home() / ".config" / "odessy" / "commands.toml"


def commands_file(environ: Mapping[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    explicit = source.get(COMMANDS_FILE_ENV, "").strip()
    return Path(explicit).expanduser() if explicit else default_commands_file()


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def load_preapproved_commands(
    workspace_root: Path,
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[tuple[str, ...], ...]:
    """The approved command prefixes for ``workspace_root``, as lowercase word tuples."""

    target = path or commands_file(environ)
    if not target.is_file():
        return ()
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise CommandsConfigError(f"{target}: cannot read commands file: {error}") from error

    unknown = sorted(set(data) - {"workspaces"})
    if unknown:
        raise CommandsConfigError(f"{target}: unknown keys {unknown}")
    workspaces = data.get("workspaces", [])
    if not isinstance(workspaces, list):
        raise CommandsConfigError(f"{target}: use [[workspaces]] entries")

    approved: list[tuple[str, ...]] = []
    for index, entry in enumerate(workspaces, start=1):
        where = f"{target}: workspaces entry {index}"
        if not isinstance(entry, dict) or set(entry) - {"path", "commands"}:
            raise CommandsConfigError(f"{where}: needs only path and commands")
        raw_path, commands = entry.get("path"), entry.get("commands", [])
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise CommandsConfigError(f"{where}: path must be the workspace folder")
        if not isinstance(commands, list) or not all(isinstance(item, str) for item in commands):
            raise CommandsConfigError(f"{where}: commands must be a list of strings")
        prefixes = [_parse_command(where, item) for item in commands]
        if _same_path(Path(raw_path).expanduser(), workspace_root):
            approved.extend(prefixes)
    return tuple(dict.fromkeys(approved))


def _parse_command(where: str, command: str) -> tuple[str, ...]:
    words = tuple(command.lower().split())
    if not words:
        raise CommandsConfigError(f"{where}: an empty command")
    if SHELL_SYNTAX.search(command):
        raise CommandsConfigError(f"{where}: {command!r} contains shell syntax (chaining, redirection or variables)")
    if len(words) == 1 and words[0] in _TOO_BROAD:
        raise CommandsConfigError(
            f"{where}: {command!r} alone would approve anything it runs; name the script, e.g. '{words[0]} test'"
        )
    return words


def matches_preapproved(command: str, approved: tuple[tuple[str, ...], ...]) -> bool:
    """Whether ``command`` starts with an approved entry's words and has no shell syntax."""

    if not approved or SHELL_SYNTAX.search(command):
        return False
    words = tuple(command.lower().split())
    return any(words[: len(entry)] == entry for entry in approved)
