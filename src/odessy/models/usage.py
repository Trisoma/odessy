"""Cloud token usage for one run: what was spent, what it cost, and a ceiling.

Every cloud model call is recorded here, by every adapter and router in the process,
so a voice session that runs for hours has one account. Three numbers per call:

* ``prompt``: all input tokens, cached or not;
* ``cached``: the part of ``prompt`` served from the provider's prompt cache (billed
  at a fraction of the input rate);
* ``completion``: output tokens.

**Cost** is estimated only from prices the user configures, per million tokens
(``ODESSY_CLOUD_PRICE_INPUT``, ``..._CACHED_INPUT``, ``..._OUTPUT``, and a label in
``ODESSY_CLOUD_PRICE_CURRENCY``). Odessy ships no price table: prices change and
differ by provider, region and plan, and a stale built-in number would be worse than
none. Without prices, only tokens are shown.

**The budget** (``ODESSY_CLOUD_BUDGET_TOKENS``, and ``ODESSY_CLOUD_BUDGET_COST`` when
prices are set) is checked before each cloud request. Once it is reached, no further
request is sent this run: the turn ends with a clear error instead of spending more.
A warning is printed once at 80%. The budget counts all tokens, cached or not, so it
is a ceiling on volume, not on the bill.
"""

from __future__ import annotations

import math
import os
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

BUDGET_TOKENS_ENV = "ODESSY_CLOUD_BUDGET_TOKENS"
BUDGET_COST_ENV = "ODESSY_CLOUD_BUDGET_COST"
PRICE_ENVS = ("ODESSY_CLOUD_PRICE_INPUT", "ODESSY_CLOUD_PRICE_CACHED_INPUT", "ODESSY_CLOUD_PRICE_OUTPUT")
CURRENCY_ENV = "ODESSY_CLOUD_PRICE_CURRENCY"
#: Share of a budget at which a one-time warning is printed.
WARN_AT = 0.8


class CloudUsageConfigError(ValueError):
    """A price or budget setting is not a non-negative number."""


class CloudBudgetExceeded(RuntimeError):
    """The run's cloud budget is spent; no further request is sent."""


@dataclass(frozen=True)
class Pricing:
    """Prices per million tokens, in the user's currency."""

    input: float
    cached_input: float
    output: float
    currency: str = "USD"

    def cost(self, *, prompt: int, cached: int, completion: int) -> float:
        uncached = max(prompt - cached, 0)
        return (uncached * self.input + cached * self.cached_input + completion * self.output) / 1_000_000


@dataclass
class Totals:
    calls: int = 0
    prompt: int = 0
    cached: int = 0
    completion: int = 0

    @property
    def tokens(self) -> int:
        return self.prompt + self.completion

    def add(self, *, prompt: int, cached: int, completion: int) -> None:
        self.calls += 1
        self.prompt += prompt
        self.cached += min(cached, prompt) if prompt else cached
        self.completion += completion


def _number(source: Mapping[str, str], name: str) -> float | None:
    raw = source.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise CloudUsageConfigError(f"{name} must be a number (got {raw!r})") from None
    if not math.isfinite(value):
        raise CloudUsageConfigError(f"{name} must be finite (got {raw!r})")
    if value < 0:
        raise CloudUsageConfigError(f"{name} must not be negative (got {raw!r})")
    return value


def pricing_from_env(environ: Mapping[str, str] | None = None) -> Pricing | None:
    """The configured prices, or None. Input and output prices are required together."""

    source = os.environ if environ is None else environ
    input_price, cached_price, output_price = (_number(source, name) for name in PRICE_ENVS)
    if input_price is None and output_price is None and cached_price is None:
        return None
    if input_price is None or output_price is None:
        raise CloudUsageConfigError(
            f"set both {PRICE_ENVS[0]} and {PRICE_ENVS[2]} (per million tokens), or neither"
        )
    return Pricing(
        input=input_price,
        cached_input=input_price if cached_price is None else cached_price,
        output=output_price,
        currency=(source.get(CURRENCY_ENV) or "USD").strip() or "USD",
    )


@dataclass
class UsageLedger:
    """The run's cloud usage. Thread-safe: generation runs in worker threads."""

    pricing: Pricing | None = None
    budget_tokens: int | None = None
    budget_cost: float | None = None
    warn: Callable[[str], None] = field(default=lambda message: print(message, file=sys.stderr, flush=True))
    total: Totals = field(default_factory=Totals)
    turn: Totals = field(default_factory=Totals)
    _warned: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> UsageLedger:
        source = os.environ if environ is None else environ
        pricing = pricing_from_env(source)
        tokens = _number(source, BUDGET_TOKENS_ENV)
        cost = _number(source, BUDGET_COST_ENV)
        if cost is not None and pricing is None:
            raise CloudUsageConfigError(f"{BUDGET_COST_ENV} needs prices: set {PRICE_ENVS[0]} and {PRICE_ENVS[2]}")
        return cls(pricing=pricing, budget_tokens=None if tokens is None else int(tokens), budget_cost=cost)

    # -- recording ------------------------------------------------------------

    def record(self, *, prompt: int | None, cached: int | None, completion: int | None) -> None:
        with self._lock:
            values = dict(prompt=prompt or 0, cached=cached or 0, completion=completion or 0)
            self.total.add(**values)
            self.turn.add(**values)
            message = self._warning()
        if message:
            self.warn(message)

    def start_turn(self) -> None:
        with self._lock:
            self.turn = Totals()

    def cost(self, totals: Totals | None = None) -> float | None:
        if self.pricing is None:
            return None
        totals = totals or self.total
        return self.pricing.cost(prompt=totals.prompt, cached=totals.cached, completion=totals.completion)

    # -- the budget -----------------------------------------------------------

    def _spent_share(self) -> float:
        shares = []
        if self.budget_tokens:
            shares.append(self.total.tokens / self.budget_tokens)
        if self.budget_cost:
            shares.append((self.cost() or 0.0) / self.budget_cost)
        return max(shares, default=0.0)

    def _exhausted(self) -> bool:
        if self.budget_tokens is not None and self.total.tokens >= self.budget_tokens:
            return True
        cost = self.cost()
        return self.budget_cost is not None and cost is not None and cost >= self.budget_cost

    def _warning(self) -> str | None:
        if self._warned or self._exhausted() or self._spent_share() < WARN_AT:
            return None
        self._warned = True
        return f"[odessy] cloud budget {self._spent_share():.0%} used: {self.budget_line()}"

    def check(self) -> None:
        """Raise ``CloudBudgetExceeded`` when the budget is spent. Called before each request."""

        with self._lock:
            if not self._exhausted():
                return
            line = self.budget_line()
        raise CloudBudgetExceeded(
            f"Cloud budget reached ({line}); no further cloud request is sent this run. "
            f"Raise {BUDGET_TOKENS_ENV} or {BUDGET_COST_ENV}, or start a new run."
        )

    # -- reporting ------------------------------------------------------------

    def _format(self, totals: Totals) -> str:
        text = f"{totals.prompt:,} in"
        if totals.cached:
            text += f" ({totals.cached:,} cached)"
        text += f", {totals.completion:,} out"
        cost = self.cost(totals)
        if cost is not None and self.pricing is not None:
            text += f", ~{cost:.4f} {self.pricing.currency}"
        return text

    def budget_line(self) -> str:
        parts = []
        if self.budget_tokens is not None:
            parts.append(f"{self.total.tokens:,} of {self.budget_tokens:,} tokens")
        if self.budget_cost is not None and self.pricing is not None:
            parts.append(f"~{self.cost() or 0.0:.4f} of {self.budget_cost:g} {self.pricing.currency}")
        return "; ".join(parts) or "no budget set"

    def turn_line(self) -> str | None:
        """One line for the turn just finished, or None when it used no cloud call."""

        with self._lock:
            if not self.turn.calls:
                return None
            line = f"[odessy] cloud usage: this turn {self._format(self.turn)} ({self.turn.calls} call(s))"
            if self.total.calls != self.turn.calls:
                line += f"; run {self._format(self.total)}"
            if self.budget_tokens is not None or self.budget_cost is not None:
                line += f"; budget {self.budget_line()}"
            return line

    def session_line(self) -> str | None:
        with self._lock:
            if not self.total.calls:
                return None
            return f"[odessy] cloud usage this run: {self._format(self.total)} ({self.total.calls} call(s))"


_process_ledger: UsageLedger | None = None
_process_lock = threading.Lock()


def process_ledger() -> UsageLedger:
    """The ledger shared by every cloud adapter in this process, read from the environment once."""

    global _process_ledger
    with _process_lock:
        if _process_ledger is None:
            _process_ledger = UsageLedger.from_env()
        return _process_ledger


def reset_process_ledger(ledger: UsageLedger | None = None) -> None:
    """Replace the shared ledger (tests, or a new run in the same process)."""

    global _process_ledger
    with _process_lock:
        _process_ledger = ledger
