"""Asking the user in the terminal whether a tool call may run.

A confirmation is only worth something if it is read. So the prompt asks for as little
as possible and makes what it does ask readable:

* **Two tiers.** A high-risk call (installing packages, a destructive third-party tool,
  anything the policy rates ``high``) must be answered by typing ``yes``; its prompt
  looks different and offers no shortcut. Anything else takes ``y``.
* **Allow for this session.** ``a`` allows the same call again without asking until
  Odessy exits: the exact same command (same arguments, same directory), or every call
  to one third-party tool; ``f`` allows every write under one folder. It is held in
  memory only and never written to a file, so it relaxes nothing the user did not
  answer this run. A session answer covers the same rule's question only: high-risk
  calls never offer it, and neither does a confirmation a hook or rule asked for.
* **Writes show what they change.** ``filesystem.write`` shows a diff against the
  current file instead of a byte count.
* **One question for a batch.** When the model proposes several calls together, the
  ones needing confirmation are listed and answered at once: all, none, or one by one.
  High-risk calls are left out of "all" and asked on their own.

What the model wrote (commands, file contents) is shown with control characters
escaped, so a terminal escape sequence cannot hide or rewrite part of the prompt.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from odessy.authority.workspace_authority import WorkspaceAuthority, WorkspaceAuthorityError
from odessy.runtime.types import PolicyDecision, ToolRequest
from odessy.tools.external_tools import external_tool

#: Lines of a write preview shown before it is cut short.
PREVIEW_LINES = 40
#: The same, per write, inside a batch list.
BATCH_PREVIEW_LINES = 12
#: Existing files larger than this are summarized, not diffed.
MAX_DIFF_BYTES = 1_000_000

_COMMAND_TOOLS = frozenset({"terminal.run", "laptop.terminal.run"})


def printable(text: str) -> str:
    """``text`` with control characters (other than tab) shown as escapes."""

    return "".join(
        char if char == "\t" or (char.isprintable() and char not in "\x1b\x9b") else repr(char)[1:-1]
        for char in text
    )


def session_key(request: ToolRequest, decision: PolicyDecision | None = None) -> tuple[str, ...] | None:
    """What "allow for this session" covers for this call, or None if it is not offered.

    A command is allowed again only exactly: same tool, same arguments with the command's
    whitespace normalized. A third-party tool is allowed as a whole, because its calls
    rarely repeat exactly. File writes are covered by folder (``write_folder``), not
    here: each one is a different change.

    The key holds the rule that asked. An answer to "this may modify state" says nothing
    about a question a hook or rule raises later in the session, and one of those is
    never covered: a user who wrote a rule to be asked must be asked.
    """

    rule = (decision.rule if decision is not None else None) or ""
    if rule.startswith("hook:"):
        return None
    if request.name in _COMMAND_TOOLS:
        arguments = dict(request.arguments)
        command = arguments.get("command")
        if isinstance(command, str):
            arguments["command"] = " ".join(command.split())
        return ("command", request.name, json.dumps(arguments, sort_keys=True, default=str), rule)
    if external_tool(request.name) is not None:
        return ("external", request.name, rule)
    return None


def write_folder(workspace_root: Path, request: ToolRequest, decision: PolicyDecision) -> str | None:
    """The folder a write sits in, relative to the workspace, if a folder answer may
    cover it. The workspace root itself is never offered: that is every file."""

    if request.name != "filesystem.write" or decision.risk == "high" or (decision.rule or "").startswith("hook:"):
        return None
    path = request.arguments.get("path")
    if not isinstance(path, str):
        return None
    try:
        relative = WorkspaceAuthority(workspace_root).resolve_path(path, must_exist=False).relative
    except WorkspaceAuthorityError:
        return None
    folder = PurePosixPath(relative).parent.as_posix()
    return None if folder == "." else folder


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
        self._write_folders: set[str] = set()

    def _covered(self, decision: PolicyDecision, request: ToolRequest) -> bool:
        """Whether an answer already given this session covers this call."""

        if decision.risk == "high":
            return False
        key = session_key(request, decision)
        if key is not None and key in self._session:
            return True
        folder = write_folder(self._workspace_root, request, decision)
        if folder is None:
            return False
        return any(folder == granted or folder.startswith(granted + "/") for granted in self._write_folders)

    def __call__(self, decision: PolicyDecision, request: ToolRequest) -> bool:
        high = decision.risk == "high"
        key = None if high else session_key(request, decision)
        if self._covered(decision, request):
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
            folder = write_folder(self._workspace_root, request, decision)
            if folder is not None:
                answer = self._ask(f"Allow? [y]es / [N]o / [f]older: every write under {printable(folder)}/ until Odessy exits: ")
                answer = answer.strip().lower()
                if answer in {"f", "folder"}:
                    self._write_folders.add(folder)
                    return True
                return answer in {"y", "yes"}
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

    def batch(self, items: list[tuple[PolicyDecision, ToolRequest]]) -> list[bool | None]:
        """One question for several calls. ``None`` means "ask about this one when reached".

        Calls already allowed for the session are answered without being listed;
        high-risk calls are listed but always asked on their own.
        """

        replies: list[bool | None] = [None] * len(items)
        listed: list[int] = []
        for index, (decision, request) in enumerate(items):
            if self._covered(decision, request):
                replies[index] = True
            elif decision.risk != "high":
                listed.append(index)
        if len(listed) < 2:
            return replies

        say = self._say
        say(f"Odessy wants to run {len(items)} tool calls that need confirmation:")
        for number, (decision, request) in enumerate(items, start=1):
            index = number - 1
            if replies[index] is True:
                say(f"  {number}. {request.name} (allowed for this session)")
                continue
            tag = "HIGH RISK, asked separately" if decision.risk == "high" else decision.risk
            say(f"  {number}. {request.name} [{tag}]")
            path, content = request.arguments.get("path"), request.arguments.get("content")
            if request.name == "filesystem.write" and isinstance(path, str) and isinstance(content, str):
                for line in write_preview(self._workspace_root, path, content, max_lines=BATCH_PREVIEW_LINES):
                    say(f"     {line}")
            else:
                say(f"     {summarize_arguments(request.arguments)}")
        say("A call that is declined stops the calls after it.")
        try:
            answer = self._ask("Run them? [y] all listed / [N] none / [s] ask one by one: ").strip().lower()
        except EOFError:
            answer = "n"
        if answer in {"s", "step"}:
            return replies
        approved = answer in {"y", "yes", "all"}
        for index in listed:
            replies[index] = approved
        return replies
