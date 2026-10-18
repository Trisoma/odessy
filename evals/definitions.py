"""Evaluation case definitions, loaded from ``evals/cases/*.toml``.

Each file holds ``[[case]]`` tables for one category. Loading validates every case
up front (required fields per category, regexes compile, tool names exist), so a
typo fails loudly instead of quietly scoring every attempt as a miss.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from odessy.tool_contracts import TOOL_REQUEST_SCHEMAS

from evals.toml_compat import loads_toml

Category = Literal["safety", "commands", "tools", "multistep"]
CATEGORIES: tuple[Category, ...] = ("safety", "commands", "tools", "multistep")
CASES_DIR = Path(__file__).parent / "cases"

#: Constructs that do not exist in Windows PowerShell 5.1, and PowerShell cmdlets
#: that do not exist in sh. A command for the wrong shell fails even if it would
#: "look" right.
SHELL_FORBIDDEN: dict[str, tuple[str, ...]] = {
    "powershell": (
        r"\bgrep\b",
        r"\bwc\s+-l\b",
        r"\|\s*head\b",
        r"\|\s*tail\b",
        r"\bawk\b",
        r"\bsed\b",
        r"\bxargs\b",
        r"\$\(",
        r"/proc/",
        # sh-style variables. In PowerShell `$PATH` is an unset variable and prints
        # nothing; the environment is `$env:PATH`.
        r"(?i)\$(path|home|user)\b",
    ),
    "sh": (r"\b(Get|Select|Sort|Where|Measure|Set|New|Remove|Test|Invoke)-[A-Z][A-Za-z]+",),
}


@dataclass(frozen=True)
class Matcher:
    """Matches one tool call: ``tool`` is a full-match regex on the tool name;
    ``pattern`` (optional) is searched in the call's argument values."""

    tool: str
    pattern: str | None = None

    def matches(self, name: str, arguments: dict[str, Any]) -> bool:
        if not re.fullmatch(self.tool, name):
            return False
        if self.pattern is None:
            return True
        return re.search(self.pattern, argument_text(arguments)) is not None


def argument_text(arguments: dict[str, Any]) -> str:
    """Argument values joined as plain text, so patterns see paths and commands as
    written rather than JSON-escaped."""

    return " ".join(str(value) for value in arguments.values())


@dataclass(frozen=True)
class Case:
    id: str
    category: Category
    prompt: str
    notes: str = ""
    #: safety: "dangerous" or "benign".
    kind: str | None = None
    #: safety/dangerous: calls that must never reach execution.
    forbidden: tuple[Matcher, ...] = ()
    #: safety/benign: at least one of these must reach execution.
    expect_reached: tuple[Matcher, ...] = ()
    #: tools: acceptable first tools ("none" = answer without a tool).
    expect_tools: tuple[str, ...] = ()
    #: tools: per-argument regexes the first call must match.
    expect_args: dict[str, str] = field(default_factory=dict)
    #: commands: acceptable tools; terminal tools are graded on the command text.
    accept_tools: tuple[str, ...] = ()
    #: commands: "powershell" (the laptop) or "sh" (the Jetson); None = either.
    shell: str | None = None
    #: commands: alternatives; each is a list of regexes that must ALL match.
    command_patterns: tuple[tuple[str, ...], ...] = ()
    #: multistep: ordered tool-name regexes that must appear among executed calls.
    sequence: tuple[str, ...] = ()
    #: multistep: substrings the final answer must contain (case-insensitive).
    answer_contains: tuple[str, ...] = ()
    #: Optional stdout the recording server returns for terminal tools.
    terminal_stdout: str | None = None


class CaseError(ValueError):
    pass


def load_cases(directory: Path = CASES_DIR) -> list[Case]:
    cases: list[Case] = []
    for path in sorted(directory.glob("*.toml")):
        data = loads_toml(path.read_text(encoding="utf-8"))
        for raw in data.get("case", []):
            cases.append(_parse(raw, path.name))
    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        raise CaseError(f"duplicate case ids: {duplicates}")
    return cases


def _parse(raw: dict[str, Any], source: str) -> Case:
    where = f"{source}:{raw.get('id', '?')}"
    try:
        case = Case(
            id=raw["id"],
            category=raw["category"],
            prompt=raw["prompt"],
            notes=raw.get("notes", ""),
            kind=raw.get("kind"),
            forbidden=tuple(Matcher(**item) for item in raw.get("forbidden", [])),
            expect_reached=tuple(Matcher(**item) for item in raw.get("expect_reached", [])),
            expect_tools=tuple(raw.get("expect_tools", [])),
            expect_args=dict(raw.get("expect_args", {})),
            accept_tools=tuple(raw.get("accept_tools", [])),
            shell=raw.get("shell"),
            command_patterns=tuple(tuple(group) for group in raw.get("command_patterns", [])),
            sequence=tuple(raw.get("sequence", [])),
            answer_contains=tuple(raw.get("answer_contains", [])),
            terminal_stdout=raw.get("terminal_stdout"),
        )
    except (KeyError, TypeError) as exc:
        raise CaseError(f"{where}: malformed case ({exc})") from exc
    _validate(case, where)
    return case


def _validate(case: Case, where: str) -> None:
    def fail(message: str) -> None:
        raise CaseError(f"{where}: {message}")

    if case.category not in CATEGORIES:
        fail(f"unknown category {case.category!r}")
    if not case.prompt.strip():
        fail("empty prompt")

    regexes = [m.tool for m in (*case.forbidden, *case.expect_reached)]
    regexes += [m.pattern for m in (*case.forbidden, *case.expect_reached) if m.pattern]
    regexes += list(case.expect_args.values()) + list(case.sequence)
    regexes += [pattern for group in case.command_patterns for pattern in group]
    for pattern in regexes:
        try:
            re.compile(pattern)
        except re.error as exc:
            fail(f"bad regex {pattern!r}: {exc}")

    known = set(TOOL_REQUEST_SCHEMAS)
    for name in (*case.expect_tools, *case.accept_tools):
        if name != "none" and name not in known:
            fail(f"unknown tool {name!r}")
    for matcher in (*case.forbidden, *case.expect_reached):
        if not any(re.fullmatch(matcher.tool, name) for name in known):
            fail(f"tool regex {matcher.tool!r} matches no tool")

    if case.category == "safety":
        if case.kind == "dangerous" and not case.forbidden:
            fail("dangerous safety case needs `forbidden`")
        elif case.kind == "benign" and not case.expect_reached:
            fail("benign safety case needs `expect_reached`")
        elif case.kind not in {"dangerous", "benign"}:
            fail("safety case needs kind = 'dangerous' or 'benign'")
    elif case.category == "tools":
        if not case.expect_tools:
            fail("tools case needs `expect_tools`")
    elif case.category == "commands":
        if not case.accept_tools:
            fail("commands case needs `accept_tools`")
        terminal = {"terminal.run", "laptop.terminal.run"} & set(case.accept_tools)
        if terminal and not case.command_patterns:
            fail("commands case with a terminal tool needs `command_patterns`")
        if case.shell not in {None, "powershell", "sh"}:
            fail(f"unknown shell {case.shell!r}")
    elif case.category == "multistep":
        if not case.sequence or not case.answer_contains:
            fail("multistep case needs `sequence` and `answer_contains`")
