"""The single registry of context-budget thresholds.

Today these thresholds live in several modules (cloud_model.py,
application/context/service.py, runtime/memory.py). This registry mirrors their
default values in one place so the coordination layer can reason about a single
budget without importing or changing any consumer. It is a *read-only mirror*:
the existing modules keep their own constants; this registry does not override
them, and changing a value here does not change how compression behaves.

Consumers opt in by passing a registry to the ledger. The registry is inert by
default: ``enabled`` is off unless ``ODESSY_CONTEXT_BUDGET_GUARD=on``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

#: Defaults mirrored from the current producers. Kept in sync by tests so a
#: drift in either place is caught rather than silently mis-budgeted.
DEFAULT_CLOUD_CONTEXT_CHARS = 24000       # models/cloud_model.py
DEFAULT_CLOUD_RESULT_CHARS = 16000        # models/cloud_model.py
DEFAULT_KEEP_RECENT_RESULTS = 2           # models/cloud_model.py
DEFAULT_PREPARED_CONTEXT_CHARS = 24000    # application/context/service.py
DEFAULT_MEMORY_CHARS = 8000               # runtime/memory.py
DEFAULT_MEMORY_EXCHANGES = 6              # runtime/memory.py
DEFAULT_NUM_CTX = 8192                    # models/ollama_model.py
DEFAULT_WINDOW_TOKENS = 0                 # 0 = no window guard

GUARD_ENV = "ODESSY_CONTEXT_BUDGET_GUARD"
WINDOW_TOKENS_ENV = "ODESSY_CONTEXT_WINDOW_TOKENS"
CHARS_PER_TOKEN_ENV = "ODESSY_CONTEXT_CHARS_PER_TOKEN"


def _on(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() not in {"", "0", "off", "false", "no"}


def _non_negative_int(raw: object, default: int) -> int:
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


@dataclass(frozen=True)
class BudgetRegistry:
    """Thresholds and feature switch for the context-budget coordination layer.

    ``enabled`` off means the layer is a no-op everywhere: no recording, no guard.
    ``window_tokens`` is the model context window to guard against; 0 disables the
    window check (the caller may still record usage for observability).
    """

    enabled: bool = False
    window_tokens: int = DEFAULT_WINDOW_TOKENS
    chars_per_token: int = 3
    cloud_context_chars: int = DEFAULT_CLOUD_CONTEXT_CHARS
    cloud_result_chars: int = DEFAULT_CLOUD_RESULT_CHARS
    keep_recent_results: int = DEFAULT_KEEP_RECENT_RESULTS
    prepared_context_chars: int = DEFAULT_PREPARED_CONTEXT_CHARS
    memory_chars: int = DEFAULT_MEMORY_CHARS
    memory_exchanges: int = DEFAULT_MEMORY_EXCHANGES
    local_num_ctx: int = DEFAULT_NUM_CTX

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "BudgetRegistry":
        env = environ if environ is not None else _process_env()
        return cls(
            enabled=_on(env.get(GUARD_ENV)),
            window_tokens=_non_negative_int(env.get(WINDOW_TOKENS_ENV), DEFAULT_WINDOW_TOKENS),
            chars_per_token=max(1, _non_negative_int(env.get(CHARS_PER_TOKEN_ENV), 3)),
            cloud_context_chars=_non_negative_int(
                env.get("ODESSY_CLOUD_CONTEXT_CHARS"), DEFAULT_CLOUD_CONTEXT_CHARS
            ),
            cloud_result_chars=_non_negative_int(
                env.get("ODESSY_CLOUD_MAX_RESULT_CHARS"), DEFAULT_CLOUD_RESULT_CHARS
            ),
            keep_recent_results=_non_negative_int(
                env.get("ODESSY_CLOUD_KEEP_RECENT_RESULTS"), DEFAULT_KEEP_RECENT_RESULTS
            ),
        )


def _process_env() -> Mapping[str, str]:
    import os

    return dict(os.environ)
