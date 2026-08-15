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


ModelAction = ToolRequest | FinalResponse


@dataclass(frozen=True)
class ToolObservation:
    request: ToolRequest
    result: dict[str, Any]


@dataclass(frozen=True)
class LogEntry:
    event: str
    detail: dict[str, Any] = field(default_factory=dict)


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
