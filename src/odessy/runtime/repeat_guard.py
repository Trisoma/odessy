"""Advisory repeat-tool-call detector.

Python port of DeepSeek Harness's ``packages/guard/repeat-tool-reminder``
(``src/index.ts``). The chain semantics, the canonicalization rule, the
threshold contract, and the reminder wording are deliberately equivalent so the
two projects behave the same way in a loop.

Behaviour (mirrors upstream):

- Detection is on **exact** repeats: same tool name and same arguments,
  compared through a deep key-sorted canonical form so property order does not
  matter.
- Only **consecutive** identical calls count. A different call resets the chain.
- The reminder is **advisory**. It never blocks, vetoes, delays, or rewrites a
  call. It only enriches what the model sees next.
- Thresholds escalate gentle -> detailed. The first threshold in the configured
  list is the gentle tier.

Upstream's ``argumentsPreviewChars`` caps only the reminder text; the chain key
always compares the full canonical string. That distinction is preserved here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

#: Consecutive-repeat counts that trigger a reminder (upstream default).
DEFAULT_THRESHOLDS: tuple[int, ...] = (3, 5, 8)

#: Default cap on canonical arguments quoted in the detailed reminder.
DEFAULT_ARGUMENTS_PREVIEW_CHARS = 500

#: Gentle first-threshold reminder, keyed to ``thresholds[0]`` rather than a
#: literal count, so a custom first threshold keeps the escalation.
GENTLE_REMINDER = (
    "You are repeating the exact same tool call with identical arguments. "
    "Carefully analyze the previous result before calling again: if the task is "
    "not complete, try a different approach or different arguments instead of "
    "repeating the call."
)


def detailed_reminder(tool_name: str, count: int, canonical_arguments: str) -> str:
    """Detailed later-threshold reminder naming the tool, run length, and arguments."""

    return (
        "Repeated tool call detected:\n"
        f"- tool: {tool_name}\n"
        f"- consecutive_calls: {count}\n"
        f"- arguments: {canonical_arguments}\n"
        "The repeated calls are not making progress. Do not call this tool with "
        "these exact arguments again. Inspect the latest result and choose a "
        "different action, different arguments, or finish the task if enough "
        "evidence has been gathered."
    )


def sort_json_value(value: Any) -> Any:
    """Deep key-sort a JSON value so key order does not affect equality."""

    if isinstance(value, (list, tuple)):
        return [sort_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: sort_json_value(value[key]) for key in sorted(value)}
    return value


def canonicalize(arguments_value: Any) -> str:
    """Canonical string form of a call's arguments: deep key-sort, then dump."""

    try:
        return json.dumps(sort_json_value(arguments_value), sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        # Arguments always arrive as parsed JSON in Odessy, so this is
        # defensive only: fall back to a stable repr rather than raising into
        # the loop.
        return repr(arguments_value)


def wildcard_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile one ``*``-wildcard pattern to an anchored regex.

    Every other regex metacharacter is matched literally. Upstream notes a
    pattern may legitimately match no registered tool.
    """

    escaped = re.sub(r"([|\\{}()\[\]^$+?.])", r"\\\1", pattern)
    return re.compile("^" + escaped.replace("*", ".*") + "$")


def preview_arguments(canonical: str, cap: int) -> str:
    """Head-truncate canonical arguments for the model-visible reminder only."""

    if cap <= 0:
        return ""
    if len(canonical) <= cap:
        return canonical
    return f"{canonical[:cap]}… (+{len(canonical) - cap} more chars)"


def validate_thresholds(values: Sequence[int]) -> tuple[int, ...]:
    """Validate thresholds per upstream's fail-loud contract.

    Empty, non-integer, below 2, or duplicate values raise. Returned sorted
    ascending, because the escalation rule reads ``[0]`` as the gentle tier.
    """

    if not values:
        raise ValueError("repeat-guard: thresholds must not be empty")
    for value in values:
        if not isinstance(value, int) or isinstance(value, bool) or value < 2:
            raise ValueError(
                f"repeat-guard: invalid threshold {value!r} — every threshold must be an integer >= 2"
            )
    if len(set(values)) != len(values):
        raise ValueError("repeat-guard: thresholds must not contain duplicates")
    return tuple(sorted(values))


@dataclass(frozen=True)
class RepeatGuardConfig:
    """Guard configuration, mirroring upstream's ``Config``."""

    thresholds: tuple[int, ...] = DEFAULT_THRESHOLDS
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    arguments_preview_chars: int = DEFAULT_ARGUMENTS_PREVIEW_CHARS

    def __post_init__(self) -> None:
        object.__setattr__(self, "thresholds", validate_thresholds(self.thresholds))

    def tracks(self, tool_name: str) -> bool:
        """Whether this tool participates in the chain.

        ``exclude`` wins over ``include``; an empty ``include`` tracks every
        tool. Excluded tools are transparent: they neither count nor reset.
        """

        if any(wildcard_to_regex(p).match(tool_name) for p in self.exclude):
            return False
        if not self.include:
            return True
        return any(wildcard_to_regex(p).match(tool_name) for p in self.include)


@dataclass
class RepeatGuard:
    """Per-turn consecutive-repeat detector.

    Upstream tracks separately per agent and clears on a new user message.
    Odessy's ``run_turn`` is single-turn, so one guard instance per turn is the
    faithful equivalent; :meth:`reset` exists for when the loop becomes
    multi-turn.
    """

    config: RepeatGuardConfig = field(default_factory=RepeatGuardConfig)
    _chain_key: str | None = field(default=None, init=False, repr=False)
    _count: int = field(default=0, init=False, repr=False)

    def observe(self, tool_name: str, arguments: Any) -> str | None:
        """Record a proposed call and return a reminder when a threshold trips.

        Returns ``None`` when nothing should be added to the model's context.
        Never raises and never vetoes the call.
        """

        if not self.config.tracks(tool_name):
            return None

        key = f"{tool_name}\u0000{canonicalize(arguments)}"
        if key == self._chain_key:
            self._count += 1
        else:
            self._chain_key = key
            self._count = 1

        if self._count not in self.config.thresholds:
            return None

        if self._count == self.config.thresholds[0]:
            return GENTLE_REMINDER

        canonical = canonicalize(arguments)
        preview = preview_arguments(canonical, self.config.arguments_preview_chars)
        return detailed_reminder(tool_name, self._count, preview)

    def reset(self) -> None:
        """Clear the chain. Upstream clears on a new user message."""

        self._chain_key = None
        self._count = 0

    @property
    def consecutive_count(self) -> int:
        return self._count


def build_guard(
    *,
    thresholds: Iterable[int] | None = None,
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
    arguments_preview_chars: int = DEFAULT_ARGUMENTS_PREVIEW_CHARS,
) -> RepeatGuard:
    """Construct a guard from loose inputs, validating thresholds eagerly."""

    config = RepeatGuardConfig(
        thresholds=tuple(thresholds) if thresholds is not None else DEFAULT_THRESHOLDS,
        include=tuple(include),
        exclude=tuple(exclude),
        arguments_preview_chars=arguments_preview_chars,
    )
    return RepeatGuard(config=config)
