"""Context-budget total ledger and pre-request guard.

The ledger records *how much context each source contributed to the current
request* and, when enabled, refuses a request whose estimated size would exceed
the configured model window. It is deliberately separate from the usage ledger
(tokens/cost already spent): this one accounts for the *input size of the next
request*, which the distributed compression mechanisms otherwise let grow past
the window because each one guards only its own slice.

Safety / design rules:

* no-op by default -- with ``enabled`` off, every method returns immediately;
* thread-safe -- generation and context preparation run in worker threads;
* data-only -- stores counts and source labels, never raw text, never authority;
* per-turn accounting -- ``start_turn()`` resets contributors so a long session
  does not accumulate across turns;
* ``check()`` raises ``ContextBudgetExceeded`` (NOT a CloudUnavailableError, which
  failover would swallow) before any bytes are sent.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .estimator import estimate_messages, estimate_tokens
from .registry import BudgetRegistry


class ContextBudgetExceeded(RuntimeError):
    """The estimated request context would exceed the configured window."""


@dataclass
class ContextBudgetLedger:
    """Per-turn ledger of context contributors, with an optional pre-request guard."""

    registry: BudgetRegistry = field(default_factory=BudgetRegistry)
    _contributors: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def enabled(self) -> bool:
        return self.registry.enabled

    def start_turn(self) -> None:
        """Reset per-turn contributors. Safe no-op when disabled."""

        if not self.enabled:
            return
        with self._lock:
            self._contributors.clear()

    def record(self, source: str, tokens: int) -> None:
        """Record ``tokens`` for a named source, replacing its previous value.

        "Replace, not add" makes double-reporting harmless: the same source can be
        reported from a producer and from its consumer without double counting.
        """

        if not self.enabled or not isinstance(source, str) or not source:
            return
        try:
            amount = max(0, int(tokens))
        except (TypeError, ValueError):
            return
        with self._lock:
            self._contributors[source] = amount

    def record_chars(self, source: str, chars: int) -> None:
        """Record a numeric character count through the shared ratio.

        ``chars`` must be a number (or numeric string); a non-numeric value is
        ignored. To record the size of a piece of text, use :meth:`record_text`.
        """

        if not self.enabled:
            return
        try:
            count = max(0, int(chars))
        except (TypeError, ValueError):
            return
        self.record(source, _ceil_div(count, self.registry.chars_per_token))

    def record_text(self, source: str, text: str) -> None:
        """Record the estimated size of a text fragment."""

        if not self.enabled:
            return
        self.record(source, estimate_tokens(text, chars_per_token=self.registry.chars_per_token))

    def record_messages(self, source: str, messages: Iterable[Any]) -> None:
        """Record the estimated size of a prepared message list."""

        if not self.enabled:
            return
        tokens = estimate_messages(messages, chars_per_token=self.registry.chars_per_token)
        self.record(source, tokens)

    def snapshot(self) -> dict[str, int]:
        """A copy of the current per-turn contributors (counts only, no text)."""

        if not self.enabled:
            return {}
        with self._lock:
            return dict(self._contributors)

    def total(self) -> int:
        if not self.enabled:
            return 0
        with self._lock:
            return sum(self._contributors.values())

    def check(self) -> None:
        """Raise if the summed contributors exceed the configured window.

        Prefer :meth:`check_messages` at the actual send point: a prepared message
        list already *contains* the injected context and memory, so guarding it is
        the authoritative measure. This method is for callers that only have
        per-source counts and want a coarse guard.
        """

        if not self.enabled:
            return
        with self._lock:
            total = sum(self._contributors.values())
            breakdown = dict(self._contributors)
        self._guard(total, breakdown)

    def check_messages(self, messages: Iterable[Any]) -> None:
        """Guard the whole request from the final message list, before any send.

        This is the authoritative pre-request check. It estimates the complete
        message list once (which already includes any injected context/memory/tool
        results), so there is no double counting. No-op unless both the guard flag
        and a positive window are configured, and never mutates ``messages``.
        """

        if not self.enabled:
            return
        window = self.registry.window_tokens
        if window <= 0:
            return
        total = estimate_messages(messages, chars_per_token=self.registry.chars_per_token)
        self._guard(total, {})

    def _guard(self, total: int, breakdown: Mapping[str, int]) -> None:
        window = self.registry.window_tokens
        if window <= 0 or total <= window:
            return
        detail = ""
        if breakdown:
            detail = " (" + ", ".join(
                f"{name}={value}" for name, value in sorted(breakdown.items())
            ) + ")"
        raise ContextBudgetExceeded(
            f"estimated context {total} tokens exceeds the {window}-token window"
            f"{detail}; reduce context or raise ODESSY_CONTEXT_WINDOW_TOKENS"
        )


def _ceil_div(value: int, divisor: int) -> int:
    divisor = max(1, divisor)
    return (value + divisor - 1) // divisor


# -- process-wide ledger, mirroring usage.py's process ledger -----------------

_process_ledger: ContextBudgetLedger | None = None
_process_lock = threading.Lock()


def process_context_budget() -> ContextBudgetLedger:
    """Return the process-wide ledger, built once from the environment."""

    global _process_ledger
    with _process_lock:
        if _process_ledger is None:
            _process_ledger = ContextBudgetLedger(registry=BudgetRegistry.from_env())
        return _process_ledger


def reset_process_context_budget(registry: BudgetRegistry | None = None) -> None:
    """Replace the process ledger. Used at turn setup and in tests."""

    global _process_ledger
    with _process_lock:
        _process_ledger = ContextBudgetLedger(
            registry=registry if registry is not None else BudgetRegistry.from_env()
        )
