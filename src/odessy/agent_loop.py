from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from mcp import Client

from odessy.policy import OdessyPolicy
from odessy.types import (
    AgentTurnResult,
    FinalResponse,
    LogEntry,
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
        self._mcp_server = mcp_server
        self._confirm = confirm

    async def run_turn(self, user_text: str) -> AgentTurnResult:
        log: list[LogEntry] = [LogEntry("user_input", {"text": user_text})]
        action = self._model.propose(user_text)

        if isinstance(action, FinalResponse):
            log.append(LogEntry("model_final_response", {"text": action.text}))
            return AgentTurnResult(final_text=action.text, log=log)

        log.append(
            LogEntry(
                "model_tool_request",
                {"name": action.name, "arguments": action.arguments},
            )
        )

        preflight_decision = self._policy.evaluate(action)
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

        async with Client(self._mcp_server) as client:
            tools = await client.list_tools()

        self._validate_tool_request(action, tools)
        log.append(LogEntry("schema_validated", {"name": action.name}))

        decision = self._policy.evaluate(action)
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

        async with Client(self._mcp_server) as client:
            result = await client.call_tool(action.name, action.arguments)
            log.append(LogEntry("mcp_tool_called", {"name": action.name}))

        structured_result = self._extract_structured_result(result)
        observation = ToolObservation(request=action, result=structured_result)
        log.append(
            LogEntry(
                "tool_observation",
                {"name": action.name, "result": structured_result},
            )
        )

        final = self._model.respond_to_observation(user_text, observation)
        log.append(LogEntry("model_final_response", {"text": final.text}))

        return AgentTurnResult(
            final_text=final.text,
            log=log,
            tool_request=action,
            tool_result=structured_result,
        )

    def _is_confirmed(self, decision: Any, request: ToolRequest) -> bool:
        if callable(self._confirm):
            return bool(self._confirm(decision, request))
        return bool(self._confirm)

    def _validate_tool_request(self, request: ToolRequest, tools: Any) -> None:
        available_tools = getattr(tools, "tools", tools)
        tool_by_name = {tool.name: tool for tool in available_tools}
        tool = tool_by_name.get(request.name)
        if tool is None:
            raise ValueError(f"Unknown MCP tool requested: {request.name}")

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
