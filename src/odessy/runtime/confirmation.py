"""Asking the user in the terminal whether a tool call may run.

A confirmation is only worth something if it is read. So the prompt asks for as little
as possible and makes what it does ask readable:

* **Two tiers.** A high-risk call (installing packages, a destructive third-party tool,
  anything the policy rates ``high``) must be answered by typing ``yes``; its prompt
  looks different and offers no shortcut. Anything else takes ``y``.
* **Allow for this session.** ``a`` allows the same call again without asking until
  Odessy exits: the exact same command (same arguments, same directory), or every call
  to one third-party tool. It is held in memory only and never written to a file, so
  it relaxes nothing the user did not answer this run. High-risk calls never offer it.
* **Writes show what they change.** ``filesystem.write`` shows a diff against the
  current file instead of a byte count.

What the model wrote (commands, file contents) is shown with control characters
escaped, so a terminal escape sequence cannot hide or rewrite part of the prompt.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from odessy.runtime.types import PolicyDecision, ToolRequest
from odessy.tools.external_tools import external_tool

#: Lines of a write preview shown before it is cut short.
PREVIEW_LINES = 40
#: Existing files larger than this are summarized, not diffed.
MAX_DIFF_BYTES = 1_000_000

_COMMAND_TOOLS = frozenset({"terminal.run", "laptop.terminal.run"})


def printable(text: str) -> str:
    """``text`` with control characters (other than tab) shown as escapes."""

    return "".join(
        char if char == "\t" or (char.isprintable() and char not in "\x1b\x9b") else repr(char)[1:-1]
        for char in text
    )


def session_key(request: ToolRequest) -> tuple[str, ...] | None:
    """What "allow for this session" covers for this call, or None if it is not offered.

    A command is allowed again only exactly: same tool, same arguments with the command's
    whitespace normalized. A third-party tool is allowed as a whole, because its calls
    rarely repeat exactly. File writes are never covered: each one is a different change.
    """

    if request.name in _COMMAND_TOOLS:
        arguments = dict(request.arguments)
        command = arguments.get("command")
        if isinstance(command, str):
            arguments["command"] = " ".join(command.split())
        return ("command", request.name, json.dumps(arguments, sort_keys=True, default=str))
    if external_tool(request.name) is not None:
        return ("external", request.name)
    return None


def write_preview(workspace_root: Path, path: str, content: str, *, max_lines: int = PREVIEW_LINES) -> list[str]:
    """What writing ``content`` to ``path`` would change, as printable lines."""

    root = workspace_root.resolve()
    try:
        target = (root / path).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        return [f"writes {printable(path)} (outside the workspace; the policy decides)"]
    shown = printable(path)
    new_lines = content.splitlines()
    if not target.exists():
        header = f"creates {shown}: {len(new_lines)} line(s), {len(content.encode('utf-8'))} bytes"
        body = [f"+{line}" for line in new_lines]
    elif not target.is_file():
        return [f"writes {shown}, which is not a regular file"]
    else:
        size = target.stat().st_size
        if size > MAX_DIFF_BYTES:
            return [f"replaces {shown} ({size} bytes) with {len(content.encode('utf-8'))} bytes; too large to diff"]
        try:
            old = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return [f"replaces {shown} ({size} bytes, not UTF-8 text) with {len(content.encode('utf-8'))} bytes"]
        if old == content:
            return [f"{shown}: no change (the content is identical)"]
        body = list(
            difflib.unified_diff(old.splitlines(), new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="", n=2)
        )[2:]
        added = sum(1 for line in body if line.startswith("+"))
        removed = sum(1 for line in body if line.startswith("-"))
        header = f"changes {shown}: +{added} -{removed} line(s)"
    lines = [header, *(f"  {printable(line)}" for line in body[:max_lines])]
    if len(body) > max_lines:
        lines.append(f"  ... {len(body) - max_lines} more line(s)")
    return lines


def summarize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in arguments.items():
        if key == "content" and isinstance(value, str):
            summary[key] = f"<{len(value.encode('utf-8'))} bytes>"
        elif isinstance(value, str):
            summary[key] = printable(value)
        else:
            summary[key] = value
    return summary


class TerminalConfirmer:
    """The ``confirm`` callback for the CLI and voice loop. One instance per run."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        ask: Callable[[str], str] | None = None,
        say: Callable[[str], None] | None = None,
    ) -> None:
        self._workspace_root = workspace_root
        # Looked up at call time, so the builtins can be replaced (tests, the voice UI).
        self._ask = ask or (lambda prompt: input(prompt))
        self._say = say or (lambda line: print(line))
        self._session: set[tuple[str, ...]] = set()

    def __call__(self, decision: PolicyDecision, request: ToolRequest) -> bool:
        high = decision.risk == "high"
        key = None if high else session_key(request)
        if key is not None and key in self._session:
            self._say(f"Odessy runs {request.name} (allowed for this session).")
            return True

        say = self._say
        if high:
            say(f"!! HIGH RISK: Odessy asks to run {request.name}.")
        else:
            say(f"Odessy needs confirmation to run {request.name}.")
        external = external_tool(request.name)
        if external is not None:
            say(f"Server: {external.server.name} ({external.server.trust}), its tool {external.remote_name!r}")
        say(f"Risk: {decision.risk}")
        say(f"Reason: {printable(decision.reason)}")
        path, content = request.arguments.get("path"), request.arguments.get("content")
        if request.name == "filesystem.write" and isinstance(path, str) and isinstance(content, str):
            for line in write_preview(self._workspace_root, path, content):
                say(line)
        else:
            say(f"Arguments: {summarize_arguments(request.arguments)}")

        try:
            if high:
                return self._ask("Type 'yes' to allow it; anything else cancels: ").strip().lower() == "yes"
            if key is None:
                return self._ask("Allow this tool call? [y/N] ").strip().lower() in {"y", "yes"}
            scope = "this exact command" if key[0] == "command" else f"every {request.name} call"
            answer = self._ask(f"Allow? [y]es / [N]o / [a]lways for {scope} until Odessy exits: ").strip().lower()
        except EOFError:
            return False
        if answer in {"a", "always"}:
            self._session.add(key)
            return True
        return answer in {"y", "yes"}
