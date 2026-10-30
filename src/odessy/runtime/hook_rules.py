"""Declarative rules in hooks.toml: the common checks, without writing a program.

::

    presets = ["recommended"]

    [[rule]]
    name = "no-force-push"
    match = ["terminal.run", "laptop.terminal.run"]
    command_regex = 'git\\s+push\\b.*(--force|-f\\b)'
    decision = "deny"

    [[rule]]
    name = "confirm-ci-edits"
    match = "filesystem.write"
    path_glob = [".github/**", "*.lock"]
    decision = "confirm"
    raise_risk = "high"

    [[rule]]
    name = "mark-secret-reads"
    match = "filesystem.read"
    path_glob = ["**/*.pem", "**/*.key"]
    decision = "taint"
    taint = "secret-read"

    [[rule]]
    name = "no-network-after-secrets"
    match = ["terminal.run", "laptop.terminal.run"]
    when_taint = "secret-read"
    command_regex = '\\b(curl|wget|scp|ssh|Invoke-WebRequest|iwr)\\b'
    decision = "deny"

A rule runs in-process, before the hook programs, and like them it can only make a
decision stricter. Every condition it names must hold. ``max_per_turn`` turns a rule
into a limit: the matching calls up to that count pass, the ones after it get the
rule's decision. A ``taint`` rule decides nothing; it marks the turn once a matching
call has run, and ``when_taint`` makes another rule apply only after that.
``raise_risk`` on a confirming rule raises the call's risk, so that it needs a typed
``yes``; it can never lower it. (``risk`` is a condition: the levels the rule runs for.)

Rules see the call's arguments as the model wrote them, unredacted: they never leave
this process, unlike the JSON a hook program reads.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from odessy.runtime.types import ToolRequest

RuleDecision = Literal["deny", "confirm", "taint"]

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_RISKS = ("low", "medium", "high")
_TARGETS = ("local", "laptop")
_ESCALATION_MODES = ("workspace-write", "danger-full-access")
#: Rules per file, presets included. Rules are cheap, but a long list is a sign the
#: check belongs in a program.
MAX_RULES = 64
MAX_REGEX_CHARS = 500


class RuleConfigError(ValueError):
    """A rule is malformed. The message names the file and the rule."""


@dataclass
class TurnState:
    """What the hooks have learned during one turn: taints, and rule counts."""

    taints: set[str] = field(default_factory=set)
    counts: Counter = field(default_factory=Counter)
    #: Set by a post-tool hook or rule that ends the turn: (who, why).
    stop: tuple[str, str] | None = None


@dataclass(frozen=True)
class Rule:
    name: str
    decision: RuleDecision
    match: tuple[str, ...] = ("*",)
    risk: frozenset[str] = frozenset()
    command: re.Pattern | None = None
    arguments: tuple[tuple[str, re.Pattern], ...] = ()
    paths: tuple[str, ...] = ()
    escalation: frozenset[str] = frozenset()
    target: str | None = None
    when_taint: frozenset[str] = frozenset()
    max_per_turn: int | None = None
    taint: str | None = None
    reason: str = ""
    preset: str | None = None
    raise_risk: str | None = None

    def applies(self, request: ToolRequest, risk: str, workspace: Path, taints: set[str]) -> bool:
        """Every condition but ``max_per_turn``, which needs the turn's count."""

        if not any(fnmatch.fnmatchcase(request.name, pattern) for pattern in self.match):
            return False
        if self.risk and risk not in self.risk:
            return False
        if self.when_taint and not (self.when_taint & taints):
            return False
        if self.target is not None and call_target(request.name) != self.target:
            return False
        arguments = request.arguments
        if self.escalation and arguments.get("sandbox_permissions") not in self.escalation:
            return False
        if self.command is not None:
            command = arguments.get("command")
            if not isinstance(command, str) or not self.command.search(command):
                return False
        for name, pattern in self.arguments:
            value = arguments.get(name)
            if value is None or not pattern.search(value if isinstance(value, str) else str(value)):
                return False
        if self.paths:
            path = arguments.get("path")
            if not isinstance(path, str) or not path_matches(path, self.paths, workspace):
                return False
        return True

    def describe(self) -> str:
        return self.reason or f"the {self.name} rule"


def call_target(tool: str) -> str:
    return "laptop" if tool.startswith("laptop.") else "local"


def path_matches(path: str, patterns: tuple[str, ...], workspace: Path) -> bool:
    """``path`` against globs, as written, relative to the workspace, and absolute.

    ``*`` crosses folders (``fnmatch``), so ``.github/**`` and ``.github/*`` both cover
    the whole folder; a pattern without a ``/`` also matches the file name alone.
    """

    raw = path.replace("\\", "/")
    candidates = {raw[2:] if raw.startswith("./") else raw}
    try:
        absolute = Path(path) if Path(path).is_absolute() else Path(workspace) / path
        resolved = absolute.resolve()
        candidates.add(resolved.as_posix())
        candidates.add(resolved.relative_to(Path(workspace).resolve()).as_posix())
    except (OSError, ValueError):
        pass
    names = {PurePosixPath(candidate).name for candidate in candidates}
    fold = os.name == "nt"
    for pattern in patterns:
        pattern = pattern.replace("\\", "/")
        pool = candidates if "/" in pattern else candidates | names
        for candidate in pool:
            if fold:
                if fnmatch.fnmatch(candidate.casefold(), pattern.casefold()):
                    return True
            elif fnmatch.fnmatchcase(candidate, pattern):
                return True
    return False


def evaluate_rules(
    rules: tuple[Rule, ...],
    request: ToolRequest,
    risk: str,
    workspace: Path,
    state: TurnState | None,
) -> list[tuple[Rule, str]]:
    """The deciding rules that fire for one call: (rule, "deny" | "confirm").

    Counting happens here, so call this once per screened call.
    """

    taints = state.taints if state is not None else set()
    fired = []
    for rule in rules:
        if rule.decision == "taint" or not rule.applies(request, risk, workspace, taints):
            continue
        if rule.max_per_turn is not None:
            if state is None:
                continue
            state.counts[rule.name] += 1
            if state.counts[rule.name] <= rule.max_per_turn:
                continue
        fired.append((rule, rule.decision))
    return fired


def taints_for(rules: tuple[Rule, ...], request: ToolRequest, risk: str, workspace: Path, state: TurnState) -> list[tuple[Rule, str]]:
    """The taints the taint rules add for a call that has run."""

    return [
        (rule, rule.taint)
        for rule in rules
        if rule.decision == "taint" and rule.taint and rule.applies(request, risk, workspace, state.taints)
    ]


# --- presets -------------------------------------------------------------------

_COMMAND_TOOLS = ["terminal.run", "laptop.terminal.run"]

#: Opt-in rules for the dangerous commands most people never want an agent to run.
#: ``presets = ["recommended"]`` adds them; a rule of your own with the same name
#: replaces the preset's.
PRESETS: dict[str, list[dict[str, Any]]] = {
    "recommended": [
        {"name": "no-force-push", "match": _COMMAND_TOOLS, "decision": "deny",
         "command_regex": r"\bgit\s+push\b.*(\s--force\b|\s--force-with-lease\b|\s-f\b|\s\+\S)",
         "reason": "force-pushing rewrites shared history"},
        {"name": "no-pipe-to-shell", "match": _COMMAND_TOOLS, "decision": "deny",
         "command_regex": r"\b(curl|wget|iwr|invoke-webrequest|irm|invoke-restmethod)\b.*\|\s*(sh|bash|zsh|python\d*|iex|invoke-expression|pwsh|powershell)\b",
         "reason": "piping a download into a shell runs code nobody reviewed"},
        {"name": "no-world-writable", "match": _COMMAND_TOOLS, "decision": "deny",
         "command_regex": r"\bchmod\s+(-\S+\s+)*(0?777|a\+w|o\+w)\b",
         "reason": "world-writable permissions"},
        {"name": "no-privileged-containers", "match": _COMMAND_TOOLS, "decision": "deny",
         "command_regex": r"\b(docker|podman)\s+(run|create)\b.*(--privileged|--pid[= ]host|--net(work)?[= ]host|-v\s+/:/|--volume[= ]/:/)",
         "reason": "a privileged or host-namespace container is not a sandbox"},
        {"name": "no-package-publish", "match": _COMMAND_TOOLS, "decision": "deny",
         "command_regex": r"\b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|\b(cargo|poetry|flit|hatch|uv)\s+publish\b|\bgem\s+push\b|\bdotnet\s+nuget\s+push\b",
         "reason": "publishing a package is outward-facing and cannot be taken back"},
        {"name": "no-git-dir-removal", "match": _COMMAND_TOOLS, "decision": "deny",
         "command_regex": r"\b(rm|del|rmdir|rd|remove-item|ri)\b[^|;&]*(^|[\s/\\'\"])\.git([\s/\\'\"]|$)",
         "reason": "removing .git destroys the repository's history"},
        {"name": "no-persistence", "match": _COMMAND_TOOLS, "decision": "deny",
         "command_regex": r"\breg(\.exe)?\s+(add|delete|import)\b|\bschtasks(\.exe)?\s+/(create|change|delete)\b|\bcrontab\s+(-[er]\b|\S+$)|\bsystemctl\s+(enable|disable|mask)\b|\bset-itemproperty\b.*\bhk(lm|cu):",
         "reason": "changes to the registry, scheduled tasks or services outlive the turn"},
        {"name": "confirm-history-rewrite", "match": _COMMAND_TOOLS, "decision": "confirm",
         "command_regex": r"\bgit\s+(reset\s+--hard|clean\s+-\S*f|checkout\s+--\s|restore\s+(--source|--staged|\.)|rebase\b|filter-(branch|repo)\b|branch\s+-D\b|stash\s+(drop|clear)\b)",
         "raise_risk": "high", "reason": "discards local work or rewrites history"},
        {"name": "confirm-ci-and-hooks-edits", "match": "filesystem.write", "decision": "confirm",
         "path_glob": [".github/*", ".gitlab-ci.yml", ".git/*", ".husky/*", ".pre-commit-config.yaml"],
         "raise_risk": "high", "reason": "CI and git hook files run code on other machines or on every commit"},
        {"name": "no-full-access-on-laptop", "match": "laptop.terminal.run", "decision": "deny",
         "escalation": ["danger-full-access"],
         "reason": "the laptop never runs a command without its sandbox"},
    ],
}


# --- parsing -------------------------------------------------------------------

_KEYS = {
    "name", "match", "risk", "workspace", "command_regex", "arguments", "path_glob", "escalation",
    "target", "when_taint", "max_per_turn", "decision", "taint", "reason", "raise_risk",
}


def _strings(where: str, key: str, value: Any, *, allow_empty: bool = False) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or (not value and not allow_empty) or not all(isinstance(item, str) and item for item in value):
        raise RuleConfigError(f"{where}: {key} must be a string or a list of strings")
    return value


def _regex(where: str, key: str, value: Any) -> re.Pattern:
    if not isinstance(value, str) or not value or len(value) > MAX_REGEX_CHARS:
        raise RuleConfigError(f"{where}: {key} must be a regular expression of at most {MAX_REGEX_CHARS} characters")
    try:
        return re.compile(value, re.IGNORECASE)
    except re.error as error:
        raise RuleConfigError(f"{where}: {key} is not a valid regular expression: {error}") from error


def parse_rule(where: str, entry: Any, *, preset: str | None = None) -> tuple[Rule, Path | None]:
    """One ``[[rule]]`` table, validated, with the workspace it is limited to."""

    if not isinstance(entry, dict):
        raise RuleConfigError(f"{where}: must be a table")
    unknown = sorted(set(entry) - _KEYS)
    if unknown:
        raise RuleConfigError(f"{where}: unknown keys {unknown}")
    name = entry.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        raise RuleConfigError(f"{where}: name must be 1-32 lowercase letters, digits, '-' or '_'")
    where = f"{where} ({name})"
    decision = entry.get("decision")
    if decision not in ("deny", "confirm", "taint"):
        raise RuleConfigError(f"{where}: decision must be 'deny', 'confirm' or 'taint'; a rule never allows a call")
    taint = entry.get("taint")
    if decision == "taint":
        if not isinstance(taint, str) or not _NAME.match(taint):
            raise RuleConfigError(f"{where}: a taint rule needs taint = \"<name>\" (lowercase letters, digits, '-' or '_')")
    elif taint is not None:
        raise RuleConfigError(f"{where}: taint goes with decision = \"taint\"")
    risk = _strings(where, "risk", entry.get("risk", []), allow_empty=True)
    if not set(risk) <= set(_RISKS):
        raise RuleConfigError(f"{where}: risk must list some of {list(_RISKS)}")
    raise_risk = entry.get("raise_risk")
    if raise_risk is not None and raise_risk not in _RISKS:
        raise RuleConfigError(f"{where}: raise_risk must be one of {list(_RISKS)}")
    if raise_risk is not None and decision != "confirm":
        raise RuleConfigError(f"{where}: raise_risk goes with decision = \"confirm\"")
    arguments = entry.get("arguments", {})
    if not isinstance(arguments, dict) or not all(isinstance(key, str) and key for key in arguments):
        raise RuleConfigError(f"{where}: arguments must be a table of argument name = regular expression")
    escalation = _strings(where, "escalation", entry.get("escalation", []), allow_empty=True)
    if not set(escalation) <= set(_ESCALATION_MODES):
        raise RuleConfigError(f"{where}: escalation must list some of {list(_ESCALATION_MODES)}")
    target = entry.get("target")
    if target is not None and target not in _TARGETS:
        raise RuleConfigError(f"{where}: target must be one of {list(_TARGETS)}")
    limit = entry.get("max_per_turn")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 0):
        raise RuleConfigError(f"{where}: max_per_turn must be a whole number, 0 or more")
    if limit is not None and decision == "taint":
        raise RuleConfigError(f"{where}: max_per_turn does not apply to a taint rule")
    reason = entry.get("reason", "")
    if not isinstance(reason, str):
        raise RuleConfigError(f"{where}: reason must be a string")
    workspace = entry.get("workspace")
    if workspace is not None and (not isinstance(workspace, str) or not workspace.strip()):
        raise RuleConfigError(f"{where}: workspace must be a folder path")
    rule = Rule(
        name=name,
        decision=decision,
        match=tuple(_strings(where, "match", entry.get("match", ["*"]))),
        risk=frozenset(risk),
        command=_regex(where, "command_regex", entry["command_regex"]) if "command_regex" in entry else None,
        arguments=tuple((key, _regex(where, f"arguments.{key}", value)) for key, value in arguments.items()),
        paths=tuple(_strings(where, "path_glob", entry["path_glob"])) if "path_glob" in entry else (),
        escalation=frozenset(escalation),
        target=target,
        when_taint=frozenset(_strings(where, "when_taint", entry.get("when_taint", []), allow_empty=True)),
        max_per_turn=limit,
        taint=taint,
        reason=reason[:300],
        preset=preset,
        raise_risk=raise_risk,
    )
    return rule, Path(workspace).expanduser() if workspace else None


def parse_rules(target: Path, data: dict[str, Any]) -> list[tuple[Rule, Path | None]]:
    """The file's presets and ``[[rule]]`` tables. A rule of yours replaces a preset rule of the same name."""

    presets = data.get("presets", [])
    if isinstance(presets, str):
        presets = [presets]
    if not isinstance(presets, list) or not all(isinstance(item, str) for item in presets):
        raise RuleConfigError(f"{target}: presets must be a list of names, from {sorted(PRESETS)}")
    unknown = sorted(set(presets) - set(PRESETS))
    if unknown:
        raise RuleConfigError(f"{target}: unknown presets {unknown}; available: {sorted(PRESETS)}")
    entries = data.get("rule", [])
    if not isinstance(entries, list):
        raise RuleConfigError(f"{target}: use [[rule]] entries")
    own: list[tuple[Rule, Path | None]] = []
    names: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        rule, where = parse_rule(f"{target}: rule {index}", entry)
        if rule.name in names:
            raise RuleConfigError(f"{target}: two rules are named {rule.name!r}")
        names.add(rule.name)
        own.append((rule, where))
    inherited = [
        parse_rule(f"preset {preset}", entry, preset=preset)
        for preset in dict.fromkeys(presets)
        for entry in PRESETS[preset]
        if entry["name"] not in names
    ]
    rules = inherited + own
    if len(rules) > MAX_RULES:
        raise RuleConfigError(f"{target}: at most {MAX_RULES} rules, presets included")
    return rules


__all__ = [
    "MAX_RULES",
    "PRESETS",
    "Rule",
    "RuleConfigError",
    "TurnState",
    "call_target",
    "evaluate_rules",
    "parse_rule",
    "parse_rules",
    "path_matches",
    "taints_for",
]
