"""A recording MCP server that exposes every Odessy tool and executes none of them.

The evaluation runs the real agent loop -- model, validation, policy, confirmation --
and swaps only the execution layer for this server. Every call that reaches it is
recorded, which is the system-layer measurement: "this action got past the authority
boundary". Results come from a fixed fixture workspace and a canned git history, so
runs are deterministic and nothing on the host is ever touched.

Terminal tools are recorded and answered with a stub. They never spawn a process,
so even a command the policy wrongly allowed cannot run.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from odessy.authority.workspace_authority import is_sensitive_workspace_path
from odessy.tools.calculator import evaluate_arithmetic_expression
from odessy.tools.tool_contracts import TOOL_REQUEST_SCHEMAS

from evals.toml_compat import loads_toml

#: Fixed clock so `time.now` answers are reproducible.
FIXED_TIME = "2026-10-17T14:30:00+08:00"

#: Arguments the agent loop injects for process-spawning tools after policy.
_INJECTED_ARGUMENTS = ("mode", "isolation")

_TERMINAL_TOOLS = frozenset({"terminal.run", "laptop.terminal.run"})

#: The Node tool server's ``SKIP_DIRS`` and result ceiling. A leftover ``__pycache__``
#: in a fixture once failed every search that reached it, which the real server skips.
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".pytest_cache", "dist"})
_MAX_RESULTS = 100


@dataclass(frozen=True)
class RecordedCall:
    name: str
    #: What the model asked for.
    arguments: dict[str, Any]
    #: What the authority layer added after policy (the granted sandbox mode).
    injected: dict[str, Any] = field(default_factory=dict)
    #: The call reached the server and failed there (a wrong path). It still counts as
    #: reaching execution for safety; it does not count as a completed step.
    failed: bool = False
    #: What the server said, so a failed step can be explained from the record.
    error: str | None = None


@dataclass
class Recorder:
    calls: list[RecordedCall] = field(default_factory=list)


@dataclass(frozen=True)
class GitFixture:
    status: str
    diff: str
    changed_files: tuple[str, ...]
    commits: tuple[str, ...]
    #: What ``git.show`` prints, by full fixture hash.
    shows: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> GitFixture:
        data = loads_toml(path.read_text(encoding="utf-8"))
        return cls(
            status=data["status"],
            diff=data["diff"],
            changed_files=tuple(data["changed_files"]),
            commits=tuple(data["commits"]),
            shows=dict(data.get("shows", {})),
        )


def build_recording_server(
    workspace: Path,
    git: GitFixture,
    recorder: Recorder,
    *,
    terminal_stdout: str | None = None,
) -> MCPServer:
    """Build the server for one attempt.

    ``workspace`` is a throwaway copy of the fixture workspace. Read-only tools answer
    from it; ``filesystem.write`` and ``log.append`` are recorded and acknowledged but
    write nothing.
    """

    server = MCPServer("odessy-eval-recorder")
    handlers = _handlers(workspace.resolve(), git, terminal_stdout)
    for name, schema in TOOL_REQUEST_SCHEMAS.items():
        _register(server, name, schema, handlers[name], recorder)
    return server


def _register(
    server: MCPServer,
    name: str,
    schema: dict[str, Any],
    handler: Any,
    recorder: Recorder,
) -> None:
    required = set(schema["required"])
    parameters = [
        inspect.Parameter(
            key,
            inspect.Parameter.KEYWORD_ONLY,
            default=inspect.Parameter.empty if key in required else None,
            annotation=Any,
        )
        for key in schema["properties"]
    ]
    for key in _INJECTED_ARGUMENTS:
        if key not in schema["properties"]:
            parameters.append(
                inspect.Parameter(key, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=Any)
            )

    def tool(**kwargs: Any) -> dict[str, Any]:
        present = {key: value for key, value in kwargs.items() if value is not None}
        injected = {key: present.pop(key) for key in _INJECTED_ARGUMENTS if key in present}
        call = RecordedCall(name=name, arguments=present, injected=injected)
        recorder.calls.append(call)
        try:
            return handler(present)
        except Exception as exc:
            recorder.calls[recorder.calls.index(call)] = replace(call, failed=True, error=str(exc))
            if isinstance(exc, (OSError, ValueError, KeyError)):
                # The real tool server returns a failed call's message to the model
                # ("ENOENT: no such file ..."). Any other exception here reaches the
                # client as a bare "Error executing tool <name>", which says less than
                # the real server does and would make a wrong path look unrecoverable.
                raise ToolError(f"{name} failed: {exc}") from exc
            raise

    tool.__signature__ = inspect.Signature(parameters, return_annotation=dict[str, Any])  # type: ignore[attr-defined]
    tool.__annotations__ = {parameter.name: Any for parameter in parameters} | {"return": dict[str, Any]}
    server.add_tool(tool, name=name, description=name, structured_output=True)


def file_name_matches(name: str, pattern: str) -> bool:
    """``project.find`` matching, the same as the Node tool server's ``fileNameMatches``.

    A plain pattern is a case-insensitive substring of the name; a pattern with ``*`` or
    ``?`` is a glob on the name, using its last path segment. Any other character is
    literal. This used to strip ``*`` instead, which let ``*.py`` succeed here while the
    real server found nothing.
    """

    name, needle = name.lower(), pattern.lower()
    if "*" not in needle and "?" not in needle:
        return needle in name
    segment = re.split(r"[\\/]", needle)[-1]
    regex = re.escape(segment).replace(r"\*", ".*").replace(r"\?", ".")
    return re.fullmatch(regex, name) is not None


def _handlers(workspace: Path, git: GitFixture, terminal_stdout: str | None) -> dict[str, Any]:
    def inside(relative: str | None) -> Path:
        target = (workspace / (relative or ".")).resolve()
        if target != workspace and workspace not in target.parents:
            raise ValueError(f"path is outside the evaluation workspace: {relative}")
        # Every read tool on the real server resolves an *existing* path and fails
        # otherwise; returning "no matches" for a missing folder would be kinder than
        # the real server and hide the model's wrong path.
        if not target.exists():
            raise FileNotFoundError(f"no such file or directory: {relative}")
        return target

    def rel(path: Path) -> str:
        return path.relative_to(workspace).as_posix() or "."

    def files(root: Path) -> list[Path]:
        if root.is_file():
            # The real server's walk visits the path it was given, file or folder.
            return [root]
        return sorted(
            p for p in root.rglob("*") if p.is_file() and not _SKIP_DIRS.intersection(p.relative_to(root).parts)
        )

    def text_of(path: Path) -> str:
        # As Node reads "utf8": a binary file is noise to search, not a failure.
        return path.read_text(encoding="utf-8", errors="replace")

    def read(args: dict[str, Any]) -> dict[str, Any]:
        path = inside(args["path"])
        content = text_of(path)
        return {"path": rel(path), "content": content, "size": len(content), "status": "ok", "truncated": False}

    def list_dir(args: dict[str, Any]) -> dict[str, Any]:
        path = inside(args.get("path"))
        entries = [
            {"name": child.name, "path": rel(child), "type": "directory" if child.is_dir() else "file"}
            for child in sorted(path.iterdir())
        ]
        return {"path": rel(path), "entries": entries, "status": "ok", "truncated": False}

    def find(args: dict[str, Any]) -> dict[str, Any]:
        root = inside(args.get("path"))
        pattern = str(args["pattern"])
        limit = min(args.get("limit", _MAX_RESULTS), _MAX_RESULTS)
        matches = [rel(p) for p in files(root) if file_name_matches(p.name, pattern)][:limit]
        return {"pattern": args["pattern"], "path": rel(root), "matches": matches, "count": len(matches), "status": "ok", "truncated": len(matches) >= limit}

    def grep(args: dict[str, Any]) -> dict[str, Any]:
        """``project.grep`` as the Node tool server runs it, down to what it skips."""

        root = inside(args.get("path"))
        flags = re.IGNORECASE if args.get("ignore_case") else 0
        pattern = str(args["pattern"])
        try:
            matcher = re.compile(re.escape(pattern) if args.get("literal") else pattern, flags)
        except re.error as exc:
            raise ValueError(f"invalid regular expression {pattern!r}: {exc}") from exc
        limit = min(args.get("limit", _MAX_RESULTS), _MAX_RESULTS)
        glob = args.get("glob")
        name_pattern = re.escape(glob).replace(r"\*", ".*").replace(r"\?", ".") if glob else None
        matches: list[dict[str, Any]] = []
        for path in files(root):
            if name_pattern is not None and re.fullmatch(name_pattern, path.name) is None:
                continue
            if is_sensitive_workspace_path(rel(path)):
                continue
            try:
                text = text_of(path)
            except OSError:
                continue
            for number, line in enumerate(text_of(path).splitlines(), start=1):
                if matcher.search(line) and len(matches) < limit:
                    matches.append({"path": rel(path), "line": number, "text": line})
        return {"pattern": pattern, "path": rel(root), "matches": matches, "count": len(matches), "status": "ok", "truncated": len(matches) >= limit}

    def terminal(args: dict[str, Any]) -> dict[str, Any]:
        return {
            "stdout": terminal_stdout if terminal_stdout is not None else "(evaluation: command recorded, not executed)\n",
            "stderr": "",
            "exit_code": 0,
            "duration": 0.0,
            "status": "ok",
            "command": args["command"],
            "cwd": args.get("cwd", "."),
            "timed_out": False,
            "stdout_truncated": False,
            "stderr_truncated": False,
            "sandbox": {"provider_id": "evaluation-recorder", "enforcement": "none", "limitations": []},
        }

    def show(args: dict[str, Any]) -> dict[str, Any]:
        rev = str(args.get("rev", "HEAD"))
        wanted = git.commits[0].split()[0] if rev == "HEAD" and git.commits else rev
        for commit, text in git.shows.items():
            if commit.startswith(wanted):
                return {"status": "ok", "diff": text}
        raise ValueError(f"unknown revision: {rev}")

    def calculator(args: dict[str, Any]) -> dict[str, Any]:
        return dict(evaluate_arithmetic_expression(args["expression"]))

    handlers: dict[str, Any] = {
        "time.now": lambda _args: {"iso_time": FIXED_TIME, "timezone": "local"},
        "calculator.eval": calculator,
        "filesystem.read": read,
        "filesystem.list": list_dir,
        "filesystem.write": lambda args: {"path": args["path"], "bytes": len(str(args["content"]).encode()), "status": "ok"},
        "log.append": lambda _args: {"path": ".odessy/log.txt", "status": "ok"},
        "project.find": find,
        "project.grep": grep,
        "git.status": lambda _args: {"status": "ok", "raw": git.status},
        "git.diff": lambda _args: {"status": "ok", "diff": git.diff},
        "git.changed_files": lambda _args: {"status": "ok", "files": list(git.changed_files)},
        "git.log": lambda args: {
            "status": "ok",
            "commits": list(git.commits[: int(args.get("limit", 20))]),
            "limit": int(args.get("limit", 20)),
        },
        "git.show": show,
    }
    for name in _TERMINAL_TOOLS:
        handlers[name] = terminal
    missing = set(TOOL_REQUEST_SCHEMAS) - set(handlers)
    if missing:
        raise RuntimeError(f"recording server has no handler for: {sorted(missing)}")
    return handlers
