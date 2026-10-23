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

from odessy.tools.tool_contracts import TOOL_REQUEST_SCHEMAS

from evals.toml_compat import loads_toml

Category = Literal["safety", "commands", "tools", "multistep", "hard", "external", "conversation", "batch"]
CATEGORIES: tuple[Category, ...] = (
    "safety", "commands", "tools", "multistep", "hard", "external", "conversation", "batch"
)
#: Categories graded by goal (``graders._grade_goal``): what ran, what was asked, what
#: the answer says. They exercise features added after the first five categories.
GOAL_CATEGORIES = frozenset({"external", "conversation", "batch"})
#: "any": completed or blocked both pass (asking back and being stopped are both fine).
OUTCOME_EXPECTATIONS = ("completed", "blocked", "any")
CASES_DIR = Path(__file__).parent / "cases"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

#: What each hard case measures. Reported separately, since a model can be good at
#: one and poor at another.
HARD_SUBTYPES = ("long_horizon", "recovery", "grounding", "ambiguity", "long_context")

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
    #: Fixture workspace directory and git file under evals/fixtures/.
    workspace: str = "workspace"
    git: str = "git.toml"
    #: hard: which ability the case measures (see HARD_SUBTYPES).
    subtype: str | None = None
    #: hard: regexes the final answer must ALL match (case-insensitive).
    answer_patterns: tuple[str, ...] = ()
    #: hard: regexes of which the final answer must match AT LEAST ONE.
    answer_any: tuple[str, ...] = ()
    #: hard: regexes the final answer must NOT match (a fabricated or stale value).
    answer_excludes: tuple[str, ...] = ()
    #: hard: calls that must ALL have run successfully, in any order (the files a
    #: correct answer has to be read from).
    required_calls: tuple[Matcher, ...] = ()
    #: hard: calls that must never be proposed (destructive steps on a vague request).
    must_not_propose: tuple[Matcher, ...] = ()
    #: hard: the final answer must ask the user a question instead of acting.
    ask_back: bool = False
    #: hard: model calls an efficient solution needs. Reported, not graded.
    optimal_calls: int | None = None
    #: external: simulated third-party servers offered (see evals/external_fixture.py).
    servers: tuple[str, ...] = ()
    #: conversation: earlier exchanges, oldest first, as (user, assistant) pairs.
    history: tuple[tuple[str, str], ...] = ()
    #: goal categories: calls that must have been sent to the user for confirmation.
    expect_confirmation: tuple[Matcher, ...] = ()
    #: goal categories: the outcome the attempt must end with (default "completed").
    expect_outcome: str | None = None


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
            workspace=raw.get("workspace", "workspace"),
            git=raw.get("git", "git.toml"),
            subtype=raw.get("subtype"),
            answer_patterns=tuple(raw.get("answer_patterns", [])),
            answer_any=tuple(raw.get("answer_any", [])),
            answer_excludes=tuple(raw.get("answer_excludes", [])),
            required_calls=tuple(Matcher(**item) for item in raw.get("required_calls", [])),
            must_not_propose=tuple(Matcher(**item) for item in raw.get("must_not_propose", [])),
            ask_back=bool(raw.get("ask_back", False)),
            optimal_calls=raw.get("optimal_calls"),
            servers=tuple(raw.get("servers", [])),
            history=tuple((item["user"], item["assistant"]) for item in raw.get("history", [])),
            expect_confirmation=tuple(Matcher(**item) for item in raw.get("expect_confirmation", [])),
            expect_outcome=raw.get("expect_outcome"),
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

    matchers = (
        *case.forbidden, *case.expect_reached, *case.required_calls, *case.must_not_propose, *case.expect_confirmation
    )
    regexes = [m.tool for m in matchers]
    regexes += [m.pattern for m in matchers if m.pattern]
    regexes += list(case.expect_args.values()) + list(case.sequence)
    regexes += [*case.answer_patterns, *case.answer_any, *case.answer_excludes]
    regexes += [pattern for group in case.command_patterns for pattern in group]
    for pattern in regexes:
        try:
            re.compile(pattern)
        except re.error as exc:
            fail(f"bad regex {pattern!r}: {exc}")

    known = set(TOOL_REQUEST_SCHEMAS)
    if case.servers:
        from evals.external_fixture import SERVER_NAMES, public_tool_names

        unknown_servers = set(case.servers) - set(SERVER_NAMES)
        if unknown_servers:
            fail(f"unknown fixture servers {sorted(unknown_servers)}")
        known |= {name for name in public_tool_names() if name.split(".")[0] in case.servers}
    for name in (*case.expect_tools, *case.accept_tools):
        if name != "none" and name not in known:
            fail(f"unknown tool {name!r}")
    for matcher in matchers:
        if not any(re.fullmatch(matcher.tool, name) for name in known):
            fail(f"tool regex {matcher.tool!r} matches no tool")
    if not (FIXTURES_DIR / case.workspace).is_dir():
        fail(f"no fixture workspace {case.workspace!r}")
    if not (FIXTURES_DIR / case.git).is_file():
        fail(f"no git fixture {case.git!r}")

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
    elif case.category == "hard":
        if case.subtype not in HARD_SUBTYPES:
            fail(f"hard case needs subtype in {HARD_SUBTYPES}")
        if not (case.answer_patterns or case.answer_any or case.ask_back):
            fail("hard case needs an answer check (answer_patterns, answer_any or ask_back)")
    if case.optimal_calls is not None and (not isinstance(case.optimal_calls, int) or case.optimal_calls < 1):
        fail("optimal_calls must be a positive integer")
    if case.category in GOAL_CATEGORIES:
        if case.expect_outcome not in (None, *OUTCOME_EXPECTATIONS):
            fail(f"expect_outcome must be one of {OUTCOME_EXPECTATIONS}")
        if not (case.answer_patterns or case.answer_any or case.ask_back or case.expect_confirmation
                or case.forbidden or case.expect_outcome == "blocked"):
            fail("goal case needs an answer check, expect_confirmation, forbidden, or expect_outcome = 'blocked'")
    if case.category == "external" and not case.servers:
        fail("external case needs `servers`")
    if case.category != "external" and case.servers:
        fail("only external cases take `servers`")
    if case.category == "conversation" and not case.history:
        fail("conversation case needs `history`")
    if case.category != "conversation" and case.history:
        fail("only conversation cases take `history`")
