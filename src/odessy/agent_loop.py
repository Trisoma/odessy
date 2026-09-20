from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import asdict
from typing import Any, Protocol

from mcp import Client

from odessy.policy import OdessyPolicy
from odessy.types import (
    AgentTurnMetrics,
    AgentTurnResult,
    FinalResponse,
    LogEntry,
    ModelTurnDiagnostics,
    ModelAction,
    ToolObservation,
    ToolRequest,
)


class ModelAdapter(Protocol):
    def propose(self, user_text: str) -> ModelAction:
        ...

    def respond_to_observation(
        self,
        user_text: str,
        observation: ToolObservation,
    ) -> FinalResponse:
        ...


class OdessyAgent:
    """Single-turn Odessy runtime loop with model-independent tool execution."""

    def __init__(
        self,
        *,
        model: ModelAdapter,
        policy: OdessyPolicy,
        mcp_server: Any,
        confirm: Callable[[Any, ToolRequest], bool] | bool = False,
    ) -> None:
        self._model = model
        self._policy = policy
        self._mcp_servers = _normalize_mcp_servers(mcp_server)
        self._confirm = confirm

    async def run_turn(
        self,
        user_text: str,
        *,
        status_callback: Callable[[str], None] | None = None,
    ) -> AgentTurnResult:
        turn_started = time.perf_counter()
        metrics = AgentTurnMetrics()
        log: list[LogEntry] = [LogEntry("user_input", {"text": user_text})]
        _status(status_callback, "Generating action proposal")
        proposal_started = time.perf_counter()
        action = self._model.propose(user_text)
        proposal_elapsed = _elapsed_ms(proposal_started)
        _merge_model_diagnostics(
            metrics,
            _model_diagnostics(self._model),
            fallback_proposal_generation_ms=proposal_elapsed,
        )

        if isinstance(action, FinalResponse):
            log.append(LogEntry("model_final_response", {"text": action.text}))
            return _finish_turn_result(
                final_text=action.text,
                log=log,
                metrics=metrics,
                turn_started=turn_started,
            )

        metrics.tool_called = True
        log.append(
            LogEntry(
                "model_tool_request",
                {"name": action.name, "arguments": action.arguments},
            )
        )

        _status(status_callback, "Validating action")
        policy_started = time.perf_counter()
        preflight_decision = self._policy.evaluate(action)
        _add_ms(metrics, "policy_validation_ms", _elapsed_ms(policy_started))
        if preflight_decision.action == "deny":
            log.append(
                LogEntry(
                    "policy_denied",
                    {
                        "name": action.name,
                        "risk": preflight_decision.risk,
                        "reason": preflight_decision.reason,
                    },
                )
            )
            raise PermissionError(f"Policy denied request: {preflight_decision.reason}")

        mcp_started = time.perf_counter()
        tool_registry = await self._collect_tool_registry()
        _add_ms(metrics, "mcp_execution_ms", _elapsed_ms(mcp_started))

        policy_started = time.perf_counter()
        self._validate_tool_request(action, tool_registry)
        log.append(LogEntry("schema_validated", {"name": action.name}))

        decision = self._policy.evaluate(action)
        _add_ms(metrics, "policy_validation_ms", _elapsed_ms(policy_started))
        if decision.action == "deny":
            raise PermissionError(f"Policy denied request: {decision.reason}")
        if decision.requires_confirmation:
            log.append(
                LogEntry(
                    "policy_requires_confirmation",
                    {
                        "name": action.name,
                        "risk": decision.risk,
                        "reason": decision.reason,
                    },
                )
            )
            if not self._is_confirmed(decision, action):
                raise PermissionError(
                    f"Policy requires confirmation before execution: {decision.reason}"
                )
            log.append(
                LogEntry(
                    "policy_confirmed",
                    {
                        "name": action.name,
                        "risk": decision.risk,
                        "reason": decision.reason,
                    },
                )
            )
        if not decision.allowed and not decision.requires_confirmation:
            raise PermissionError(decision.reason)
        if decision.allowed:
            log.append(
                LogEntry(
                    "policy_allowed",
                    {
                        "name": action.name,
                        "risk": decision.risk,
                        "reason": decision.reason,
                    },
                )
            )

        _status(status_callback, "Running tool")
        mcp_started = time.perf_counter()
        result = await self._call_tool(action.name, action.arguments, tool_registry)
        log.append(LogEntry("mcp_tool_called", {"name": action.name}))

        structured_result = self._extract_structured_result(result)
        _add_ms(metrics, "mcp_execution_ms", _elapsed_ms(mcp_started))
        observation = ToolObservation(request=action, result=structured_result)
        log.append(
            LogEntry(
                "tool_observation",
                {"name": action.name, "result": structured_result},
            )
        )

        _status(status_callback, "Generating response")
        final_started = time.perf_counter()
        final = self._model.respond_to_observation(user_text, observation)
        final_elapsed = _elapsed_ms(final_started)
        _merge_model_diagnostics(
            metrics,
            _model_diagnostics(self._model),
            fallback_final_generation_ms=final_elapsed,
        )
        log.append(LogEntry("model_final_response", {"text": final.text}))

        return _finish_turn_result(
            final_text=final.text,
            log=log,
            metrics=metrics,
            turn_started=turn_started,
            tool_request=action,
            tool_result=structured_result,
        )

    def _is_confirmed(self, decision: Any, request: ToolRequest) -> bool:
        if callable(self._confirm):
            return bool(self._confirm(decision, request))
        return bool(self._confirm)

    async def _collect_tool_registry(self) -> dict[str, tuple[Any, Any]]:
        if not self._mcp_servers:
            return {}

        registry: dict[str, tuple[Any, Any]] = {}
        for server in self._mcp_servers:
            async with Client(server) as client:
                tools = await client.list_tools()
            available_tools = getattr(tools, "tools", tools)
            for tool in available_tools:
                registry.setdefault(tool.name, (tool, server))
        return registry

    async def _call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        tool_registry: dict[str, tuple[Any, Any]],
    ) -> Any:
        entry = tool_registry.get(name)
        if entry is None:
            raise ValueError(f"Unknown MCP tool requested: {name}")
        _, server = entry
        async with Client(server) as client:
            return await client.call_tool(name, arguments)

    def _validate_tool_request(self, request: ToolRequest, tools: dict[str, tuple[Any, Any]]) -> None:
        entry = tools.get(request.name)
        if entry is None:
            raise ValueError(f"Unknown MCP tool requested: {request.name}")
        tool, _server = entry

        input_schema = _get_tool_input_schema(tool)
        if not isinstance(request.arguments, dict):
            raise TypeError("Tool arguments must be a JSON object")
        if input_schema is None:
            return

        properties = input_schema.get("properties", {})
        required = set(input_schema.get("required", []))
        missing = required.difference(request.arguments)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"Missing required argument(s) for {request.name}: {names}")

        unexpected = set(request.arguments).difference(properties)
        if unexpected:
            names = ", ".join(sorted(unexpected))
            raise ValueError(f"Unexpected argument(s) for {request.name}: {names}")

        for argument_name, argument_value in request.arguments.items():
            expected = properties.get(argument_name, {}).get("type")
            if expected == "string" and not isinstance(argument_value, str):
                raise TypeError(f"{argument_name} must be a string")
            if expected == "integer" and not isinstance(argument_value, int):
                raise TypeError(f"{argument_name} must be an integer")
            if expected == "boolean" and not isinstance(argument_value, bool):
                raise TypeError(f"{argument_name} must be a boolean")

    def _extract_structured_result(self, result: Any) -> dict[str, Any]:
        is_error = getattr(result, "is_error", getattr(result, "isError", False))
        if is_error:
            raise RuntimeError(f"MCP tool returned an error: {result}")

        structured = getattr(
            result,
            "structured_content",
            getattr(result, "structuredContent", None),
        )
        if not isinstance(structured, dict):
            raise TypeError("MCP tool result did not include structured content")
        return structured


def _get_tool_input_schema(tool: Any) -> dict[str, Any] | None:
    schema = getattr(tool, "input_schema", None)
    if schema is None:
        schema = getattr(tool, "inputSchema", None)
    if schema is None:
        return None
    if hasattr(schema, "model_dump"):
        schema = schema.model_dump(by_alias=True)
    if not isinstance(schema, dict):
        raise TypeError(f"Unsupported MCP tool schema type: {type(schema).__name__}")
    return schema


def _normalize_mcp_servers(mcp_server: Any) -> tuple[Any, ...]:
    if mcp_server is None:
        return ()
    if isinstance(mcp_server, Sequence) and not isinstance(mcp_server, (str, bytes, bytearray)):
        return tuple(server for server in mcp_server if server is not None)
    return (mcp_server,)


def _status(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _add_ms(metrics: AgentTurnMetrics, field_name: str, value: float | None) -> None:
    if value is None:
        return
    current = getattr(metrics, field_name)
    setattr(metrics, field_name, value if current is None else current + value)


def _model_diagnostics(model: Any) -> ModelTurnDiagnostics | None:
    diagnostics = getattr(model, "last_turn_diagnostics", None)
    if isinstance(diagnostics, ModelTurnDiagnostics):
        return diagnostics
    return None


def _merge_model_diagnostics(
    metrics: AgentTurnMetrics,
    diagnostics: ModelTurnDiagnostics | None,
    *,
    fallback_proposal_generation_ms: float | None = None,
    fallback_final_generation_ms: float | None = None,
) -> None:
    if diagnostics is None:
        _add_ms(metrics, "proposal_generation_ms", fallback_proposal_generation_ms)
        _add_ms(metrics, "final_generation_ms", fallback_final_generation_ms)
        return
    for name in (
        "proposal_generation_ms",
        "proposal_parsing_ms",
        "repair_generation_ms",
        "final_generation_ms",
        "model_total_duration_ms",
        "prompt_eval_duration_ms",
        "eval_duration_ms",
        "generation_tokens_per_sec",
    ):
        value = getattr(diagnostics, name)
        if value is not None:
            setattr(metrics, name, value)
    for name in ("prompt_eval_count", "eval_count", "thinking_token_count"):
        value = getattr(diagnostics, name)
        if value is not None:
            setattr(metrics, name, value)
    metrics.model_call_count = diagnostics.model_call_count
    metrics.thinking_exposed = diagnostics.thinking_exposed
    metrics.thinking_chars = diagnostics.thinking_chars
    metrics.response_chars = diagnostics.response_chars
    metrics.proposal_valid = diagnostics.proposal_valid
    metrics.repair_triggered = diagnostics.repair_triggered
    metrics.prompt_tokens = diagnostics.prompt_tokens
    metrics.completion_tokens = diagnostics.completion_tokens
    if metrics.proposal_generation_ms is None:
        metrics.proposal_generation_ms = fallback_proposal_generation_ms
    if metrics.final_generation_ms is None:
        metrics.final_generation_ms = fallback_final_generation_ms


def _finish_turn_result(
    *,
    final_text: str,
    log: list[LogEntry],
    metrics: AgentTurnMetrics,
    turn_started: float,
    tool_request: ToolRequest | None = None,
    tool_result: dict[str, Any] | None = None,
) -> AgentTurnResult:
    metrics.agent_turn_total_ms = _elapsed_ms(turn_started)
    log.append(LogEntry("agent_turn_metrics", asdict(metrics)))
    return AgentTurnResult(
        final_text=final_text,
        log=log,
        tool_request=tool_request,
        tool_result=tool_result,
        metrics=metrics,
    )
