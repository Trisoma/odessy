"""Per-tool execution timeout vocabulary.

Adapted from DeepSeek Harness's ``packages/guard/timeout-policy``
(``src/index.ts``). The result shape, the owning code, and the model-facing
message follow upstream; the enforcement mechanism is deliberately simpler.

Two upstream ideas are preserved because they carry real information:

1. **Per-tool budgets.** A tool declares its own limit. A tool that declares
   none is delegated unchanged. There is no single global default.
2. **Scoped attribution.** The timeout code is owned by this layer, so a
   deadline that fired *elsewhere* (an outer wrapper, the HTTP client, the
   bridge's own subprocess timeout) is never misreported as this one.

Upstream's enforcement is **cooperative**: it aborts a signal and replaces the
result only after the tool reaches quiescence, and its own documentation states
plainly that it *"can only ask a tool to stop, so a tool that ignores
cancellation keeps running."* Odessy's blocking subprocess and HTTP calls are
not cancellation-aware, so this module does not pretend otherwise -- it
classifies and reports timeouts rather than preempting them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

#: The code owned by this layer. Upstream uses the same identifier, and uses it
#: both for internal deadline classification and as the structured error code.
TOOL_TIMEOUT = "TOOL_TIMEOUT"

#: Default budgets, in seconds, by tool name.
#:
#: These mirror the values already meaningful in Odessy: ``OdessyPolicy``
#: carries ``normal_timeout=10`` / ``max_timeout=30`` and ``CommandBridge``
#: caps at ``max_timeout=30``.
DEFAULT_TOOL_TIMEOUTS: dict[str, int] = {
    "time.now": 5,
    "calculator.eval": 5,
    "terminal.run": 30,
    "laptop.terminal.run": 30,
}


def tool_timeout_result(timeout_seconds: int, *, tool_name: str | None = None) -> dict[str, Any]:
    """Build the structured result substituted when a timeout is reported.

    ``content`` is the model-facing message; ``error.code`` is the same
    ``TOOL_TIMEOUT`` this layer owns, so a retry or escalation layer can route
    on it without parsing prose.
    """

    message = f"tool call timed out after {timeout_seconds}s"
    return {
        "content": [{"type": "text", "text": f"Error: {message}"}],
        "is_error": True,
        "error": {
            "message": message,
            "info": {
                "name": "ToolTimeoutError",
                "code": TOOL_TIMEOUT,
                "tool": tool_name,
                "timeout_seconds": timeout_seconds,
            },
        },
    }


@dataclass(frozen=True)
class ToolTimeoutPolicy:
    """Resolves a per-tool budget and classifies expiry.

    Deliberately small: Odessy has no cancellation-aware execution layer, so
    this decides *what the budget is* and *what the model is told*, and leaves
    enforcement to the existing subprocess and HTTP timeouts.
    """

    timeouts: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_TOOL_TIMEOUTS))

    def budget_for(self, tool_name: str) -> int | None:
        """Return the budget in seconds, or ``None`` when the tool declares none.

        A tool with no declared budget is delegated unchanged, matching
        upstream's behaviour.
        """

        return self.timeouts.get(tool_name)

    def clamp(self, tool_name: str, requested: int | None) -> int | None:
        """Clamp a model-requested timeout to the tool's declared budget."""

        budget = self.budget_for(tool_name)
        if budget is None:
            return requested
        if requested is None:
            return budget
        return min(max(1, requested), budget)

    def classify(
        self,
        *,
        tool_name: str,
        status: str,
        elapsed_seconds: float,
    ) -> dict[str, Any] | None:
        """Map a completed tool result to a ``TOOL_TIMEOUT`` result, or ``None``.

        Only the bridge's own ``timeout`` status is classified here. A timeout
        reported by a different layer keeps its own identity rather than being
        relabelled as this one.
        """

        if status != "timeout":
            return None
        budget = self.budget_for(tool_name) or int(round(elapsed_seconds))
        return tool_timeout_result(budget, tool_name=tool_name)


@dataclass(frozen=True)
class Deadline:
    """A shared wall-clock budget for one turn.

    Odessy's measured cost makes this necessary rather than decorative: a single
    LFM tool proposal was observed at 70-115 s (``benchmark/model/20260922-073735``)
    against a 120 s Ollama HTTP timeout. A retry loop bounded only by attempt
    count can therefore outlive the run that contains it. The loop must spend
    from a shared budget and stop when it is gone.
    """

    budget_seconds: float
    _started: float | None = field(default=None, init=False, repr=False, compare=False)

    def start(self) -> "Deadline":
        object.__setattr__(self, "_started", time.monotonic())
        return self

    @property
    def elapsed(self) -> float:
        if self._started is None:
            return 0.0
        return time.monotonic() - self._started

    @property
    def remaining(self) -> float:
        return max(0.0, self.budget_seconds - self.elapsed)

    @property
    def expired(self) -> bool:
        return self.remaining <= 0.0

    def has_room_for(self, seconds: float) -> bool:
        """Whether another attempt plausibly fits, given a known cost."""

        return self.remaining >= seconds
