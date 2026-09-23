"""Pure text retention: bounded head / tail windows over UTF-8 text.

Ported from DeepSeek Harness `packages/util/output-retention` (`TextRetainer`).
That package is a *library* rather than a config-loaded plugin precisely because
retention is pure: it caps how much text is shown and reports exactly what it
withheld, and nothing else.

Deliberately carries no I/O. Persisting withheld content is a separate concern
(see `odessy/spill.py`), which is what keeps this module trivially testable and
prevents a retention bug from ever destroying data.

Two upstream semantics are preserved exactly, because both are easy to get
subtly wrong:

1. Budgets and `omitted_bytes` count **bytes, not characters or lines**. A
   cap expressed in characters would be wrong for multi-byte output.

2. The omitted count is measured **after** UTF-8 boundary trimming, never
   derived from the budget. Trimming can drop up to three extra bytes per cut;
   a notice computed from the budget would overstate what was kept and so be a
   false statement about the data.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Longest UTF-8 sequence (4 bytes) minus one. Trimming can never need to drop
#: more than this many bytes to reach a codepoint boundary.
_MAX_UTF8_LEN = 4


@dataclass(frozen=True)
class Omitted:
    """How much content the retainer withheld.

    ``exact`` means the count is precise, which byte-oriented retention always
    can be. ``unknown`` is reserved for a caller that omits content without
    counting it; it exists so downstream code never has to guess whether a
    number is trustworthy.
    """

    kind: str
    count: int


def _exact(count: int) -> Omitted:
    return Omitted(kind="exact", count=count)


def _trim_prefix_to_boundary(data: bytes) -> bytes:
    """Longest prefix of ``data`` that ends on a UTF-8 codepoint boundary.

    ``data`` is assumed to be a prefix of valid UTF-8, so only the final bytes
    can be cut. Candidates are tried longest-first, which is why the first
    success is returned immediately.
    """

    for drop in range(0, min(_MAX_UTF8_LEN, len(data)) + 1):
        candidate = data[: len(data) - drop]
        try:
            candidate.decode("utf-8")
        except UnicodeDecodeError:
            continue
        return candidate
    return b""


def _trim_suffix_to_boundary(data: bytes) -> bytes:
    """Longest suffix of ``data`` that starts on a UTF-8 codepoint boundary.

    The *end* of ``data`` is already a boundary (it is the end of the original
    text), so only leading bytes -- a partial sequence -- need dropping.
    """

    for drop in range(0, min(_MAX_UTF8_LEN, len(data))):
        candidate = data[drop:]
        try:
            candidate.decode("utf-8")
        except UnicodeDecodeError:
            continue
        return candidate
    return b""


@dataclass(frozen=True)
class RetentionResult:
    """The inline text plus the mechanical facts a notice needs."""

    text: str
    kept_prefix_bytes: int
    kept_suffix_bytes: int
    omitted_bytes: Omitted
    truncated: bool
    total_bytes: int


@dataclass(frozen=True)
class TextRetainer:
    """Head-and-tail byte budget over UTF-8 text.

    ``head_bytes`` and ``tail_bytes`` are the inline budget. Text at or under
    the combined budget is returned unchanged and uncut; larger text keeps both
    ends and reports the exact omitted byte count.
    """

    head_bytes: int = 0
    tail_bytes: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "head_bytes", max(0, int(self.head_bytes)))
        object.__setattr__(self, "tail_bytes", max(0, int(self.tail_bytes)))

    @property
    def budget_bytes(self) -> int:
        return self.head_bytes + self.tail_bytes

    def retain(self, text: str) -> RetentionResult:
        """Return ``text`` bounded to the budget, with exact omission metadata."""

        data = text.encode("utf-8")
        total = len(data)

        if total <= self.budget_bytes:
            # Nothing withheld, so prefix and suffix are adjacent slices of the
            # same buffer and no boundary trim is warranted or needed.
            return RetentionResult(
                text=text,
                kept_prefix_bytes=total,
                kept_suffix_bytes=0,
                omitted_bytes=_exact(0),
                truncated=False,
                total_bytes=total,
            )

        prefix = _trim_prefix_to_boundary(data[: self.head_bytes])
        suffix = _trim_suffix_to_boundary(data[len(data) - self.tail_bytes :])

        kept_prefix = len(prefix)
        kept_suffix = len(suffix)
        # Measured, not derived: the budget is not what was kept.
        omitted = total - kept_prefix - kept_suffix

        if omitted <= 0:
            # A pathological budget (e.g. tail larger than the text) can leave
            # the two windows overlapping. Adjacent, complete text beats a
            # negative omission count and a fabricated notice.
            return RetentionResult(
                text=text,
                kept_prefix_bytes=total,
                kept_suffix_bytes=0,
                omitted_bytes=_exact(0),
                truncated=False,
                total_bytes=total,
            )

        assert omitted > 0
        parts = [prefix.decode("utf-8")]
        if kept_suffix:
            parts.append(suffix.decode("utf-8"))
        return RetentionResult(
            text="".join(parts),
            kept_prefix_bytes=kept_prefix,
            kept_suffix_bytes=kept_suffix,
            omitted_bytes=_exact(omitted),
            truncated=True,
            total_bytes=total,
        )


def format_retention_notice(
    result: RetentionResult,
    *,
    unit_label: str = "output",
    recovery: str | None = None,
) -> str:
    """Render a consistent omission clause, optionally with recovery guidance.

    The split mirrors upstream: this module owns the *mechanical* clause (what
    was withheld and how much), while the caller owns recovery guidance, since
    only the caller knows where the full content went. A notice without a
    recovery hint is still honest, just less useful.
    """

    if not result.truncated:
        return ""
    notice = (
        f"[odessy: {unit_label} truncated -- omitted "
        f"{result.omitted_bytes.count} bytes of {result.total_bytes}]"
    )
    if recovery:
        notice = f"{notice} {recovery}"
    return notice
