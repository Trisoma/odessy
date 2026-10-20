from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ToolRequest:
    """Provider-neutral request emitted by a model adapter."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FinalResponse:
    text: str


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    request: ToolRequest


@dataclass(frozen=True)
class ToolBatch:
    calls: tuple[ToolCall, ...]

    def __post_init__(self) -> None:
        ids = [call.call_id for call in self.calls]
        if not ids or len(ids) > 16 or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise ValueError("Tool batches require 1-16 calls with unique nonempty IDs")


ModelAction = ToolRequest | ToolBatch | FinalResponse


@dataclass(frozen=True)
class ToolObservation:
    request: ToolRequest
    result: dict[str, Any]
    call_id: str | None = None


@dataclass(frozen=True)
class LogEntry:
    event: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelTurnDiagnostics:
    proposal_generation_ms: float | None = None
    proposal_parsing_ms: float | None = None
    repair_generation_ms: float | None = None
    final_generation_ms: float | None = None
    model_total_duration_ms: float | None = None
    model_load_duration_ms: float | None = None
    prompt_eval_duration_ms: float | None = None
    prompt_eval_count: int | None = None
    eval_duration_ms: float | None = None
    eval_count: int | None = None
    generation_tokens_per_sec: float | None = None
    model_call_count: int = 0
    thinking_exposed: bool = False
    thinking_chars: int = 0
    thinking_token_count: int | None = None
    response_chars: int = 0
    proposal_valid: bool = True
    repair_triggered: bool = False
    routing: dict[str, Any] | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass
class AgentTurnMetrics:
    agent_turn_total_ms: float | None = None
    proposal_generation_ms: float | None = None
    proposal_parsing_ms: float | None = None
    repair_generation_ms: float | None = None
    policy_validation_ms: float | None = None
    mcp_execution_ms: float | None = None
    final_generation_ms: float | None = None
    model_total_duration_ms: float | None = None
    model_load_duration_ms: float | None = None
    prompt_eval_duration_ms: float | None = None
    prompt_eval_count: int | None = None
    eval_duration_ms: float | None = None
    eval_count: int | None = None
    generation_tokens_per_sec: float | None = None
    model_call_count: int = 0
    thinking_exposed: bool = False
    thinking_chars: int = 0
    thinking_token_count: int | None = None
    response_chars: int = 0
    proposal_valid: bool = True
    repair_triggered: bool = False
    tool_called: bool = False
    routing: dict[str, Any] | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class PolicyDecision:
    action: Literal["allow", "confirm", "deny"]
    reason: str
    risk: Literal["low", "medium", "high"] = "low"

    @property
    def allowed(self) -> bool:
        return self.action == "allow"

    @property
    def requires_confirmation(self) -> bool:
        return self.action == "confirm"


@dataclass(frozen=True)
class AgentTurnResult:
    final_text: str
    log: list[LogEntry]
    tool_request: ToolRequest | None = None
    tool_result: dict[str, Any] | None = None
    metrics: AgentTurnMetrics = field(default_factory=AgentTurnMetrics)
    tool_observations: list[ToolObservation] = field(default_factory=list)
