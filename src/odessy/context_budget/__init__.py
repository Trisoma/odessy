"""Unified, opt-in coordination of Odessy's distributed context budgets.

This package does NOT compress anything and does NOT grant authority. It adds a
thin governance layer over the existing, layered mechanisms: one conservative
token estimate, one threshold registry, and one per-turn ledger with an optional
pre-request guard against a model's context window.

Everything is inert unless ``ODESSY_CONTEXT_BUDGET_GUARD=on``. The compression
algorithms, safety barriers, provider cache prefixes and egress shape are left
entirely untouched.
"""

from .estimator import estimate_messages, estimate_tokens
from .ledger import (
    ContextBudgetExceeded,
    ContextBudgetLedger,
    check_cloud_input,
    context_budget_turn,
    current_context_budget,
)
from .registry import BudgetRegistry

__all__ = [
    "estimate_tokens", "estimate_messages",
    "ContextBudgetLedger", "ContextBudgetExceeded",
    "check_cloud_input", "context_budget_turn", "current_context_budget",
    "BudgetRegistry",
]
