from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import asdict
from typing import Any, Protocol

from mcp import Client

from odessy.policy import OdessyPolicy
from odessy.repeat_guard import RepeatGuard, build_guard
from odessy.tool_contracts import validate_tool_arguments
from odessy.tool_timeout import Deadline, ToolTimeoutPolicy
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
    ) -> ModelAction:
        """Continue after a tool result or a rejection.

        Returns ``FinalResponse`` to conclude the turn, or ``ToolRequest`` to
        propose another call. A rejection is delivered through this same
        channel, so an adapter that can propose a tool call can also propose a
        *corrected* one.
        """
        ...


#: Instruction carried by a pre-execution rejection observation.
_REJECTION_INSTRUCTION = (
    "The tool call was rejected before execution and was NOT run. "
    "Correct the arguments and propose the tool call again, or give a final answer."
)


class OdessyAgent:
    """Single-turn Odessy runtime loop with model-independent tool execution."""

    def __init__(
        self,
        *,
        model: ModelAdapter,
        policy: OdessyPolicy,
        mcp_server: Any,
        confirm: Callable[[Any, ToolRequest], bool] | bool = False,
        max_rejections: int = 2,
        deadline_seconds: float | None = None,
        max_model_calls: int = 4,
        guard: RepeatGuard | None = None,
        timeouts: ToolTimeoutPolicy | None = None,
    ) -> None:
        self._model = model
        self._policy = policy
        self._mcp_servers = _normalize_mcp_servers(mcp_server)
        self._confirm = confirm
        #: Bounded retries on pre-execution (schema/argument) rejection. Authority
        #: decisions never retry -- see run_turn.
        self._max_rejections = max(0, max_rejections)
        #: Optional shared wall-clock budget for one turn.
        #:
        #: INTENTIONALLY DEFAULTED TO None. This is a deliberate freeze
        #: decision, not an oversight. `None` becomes an infinite budget, so the
        #: deadline never expires and `max_model_calls` is the only default
        #: protection.
        #:
        #: Why no default yet: a wrong value silently truncates legitimate
        #: turns, and no measured distribution of turn durations exists to pick
        #: one from. Odessy's proposal cost is 78-115 s on a real tool request
        #: against a 120 s HTTP timeout, so the default has to be chosen
        #: together with that timeout rather than in isolation.
        self._deadline_seconds = deadline_seconds
        #: Hard ceiling on model generations per turn, covering rejection retries,
        #: multi-tool continuation, and the adapter's own repair calls.
        self._max_model_calls = max(1, max_model_calls)
        self._guard_template = guard
        self._timeouts = timeouts or ToolTimeoutPolicy()

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

        # One wall-clock budget for the whole turn, shared by rejection retries
        # and multi-tool continuation. Unset budget -> infinite, so the deadline
        # is always present rather than conditionally checked.
        deadline = Deadline(
            self._deadline_seconds if self._deadline_seconds is not None else float("inf")
        ).start()

        model_calls = 1

        # One guard per turn: the chain is cleared by a new user message, and a
        # turn is the unit of user input here.
        guard = self._guard_template or build_guard()

        # Collected lazily, only after a policy preflight allows a request: a
        # denied request must never contact MCP at all.
        tool_registry: dict[str, tuple[Any, Any]] | None = None

        rejections = 0
        last_request: ToolRequest | None = None
        last_result: dict[str, Any] | None = None
        pending_reminders: list[str] = []

        # Single action loop. Every model-produced action -- the first proposal,
        # a rejection correction, or a follow-up call after a successful
        # execution -- passes through the same validation, policy, and execution
        # path. That is what makes a ToolRequest returned after execution safe.
        while True:
            reminder = guard.observe(action.name, action.arguments)
            if reminder is not None:
                pending_reminders.append(reminder)
                log.append(LogEntry("repeat_tool_reminder", {"name": action.name}))

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
                raise PermissionError(
                    f"Policy denied request: {preflight_decision.reason}"
                )

            # Rejections are recoverable; authority outcomes above are not.
            if tool_registry is None:
                mcp_started = time.perf_counter()
                tool_registry = await self._collect_tool_registry()
                _add_ms(metrics, "mcp_execution_ms", _elapsed_ms(mcp_started))

            try:
                self._validate_tool_request(action, tool_registry)
            except (ValueError, TypeError) as exc:
                reason = str(exc)
                rejections += 1
                log.append(
                    LogEntry(
                        "schema_rejected",
                        {"name": action.name, "reason": reason, "attempt": rejections},
                    )
                )
                if not self._may_continue(
                    rejections=rejections,
                    model_calls=model_calls,
                    deadline=deadline,
                ):
                    log.append(
                        LogEntry("schema_rejection_exhausted", {"attempts": rejections})
                    )
                    return _finish_turn_result(
                        final_text=_rejection_exhausted_text(action, reason),
                        log=log,
                        metrics=metrics,
                        turn_started=turn_started,
                        tool_request=last_request,
                        tool_result=last_result,
                    )
                next_action = self._resume(
                    user_text,
                    ToolObservation(
                        request=action,
                        result={
                            "status": "rejected",
                            "reason": reason,
                            "instruction": _REJECTION_INSTRUCTION,
                            **self._drain_reminders(pending_reminders),
                        },
                    ),
                    log=log,
                    status_callback=status_callback,
                    message="Correcting rejected tool call",
                )
                model_calls += 1
                if isinstance(next_action, FinalResponse):
                    log.append(
                        LogEntry("model_final_response", {"text": next_action.text})
                    )
                    return _finish_turn_result(
                        final_text=next_action.text,
                        log=log,
                        metrics=metrics,
                        turn_started=turn_started,
                        tool_request=last_request,
                        tool_result=last_result,
                    )
                action = next_action
                continue

            log.append(LogEntry("schema_validated", {"name": action.name}))

            policy_started = time.perf_counter()
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
            last_request = action
            last_result = structured_result

            timeout_info = self._classify_timeout(action.name, structured_result)
            if timeout_info is not None:
                log.append(
                    LogEntry("tool_timeout", {"name": action.name, **timeout_info})
                )

            observation = ToolObservation(request=action, result=structured_result)
            log.append(
                LogEntry(
                    "tool_observation",
                    {"name": action.name, "result": structured_result},
                )
            )

            _status(status_callback, "Generating response")
            final_started = time.perf_counter()
            next_action = self._model.respond_to_observation(user_text, observation)
            final_elapsed = _elapsed_ms(final_started)
            _merge_model_diagnostics(
                metrics,
                _model_diagnostics(self._model),
                fallback_final_generation_ms=final_elapsed,
            )
            model_calls += 1

            # A ToolRequest here is a legitimate multi-tool continuation, not a
            # final answer. It re-enters the loop above for validation, policy,
            # and execution like any other action.
            if isinstance(next_action, FinalResponse):
                log.append(LogEntry("model_final_response", {"text": next_action.text}))
                return _finish_turn_result(
                    final_text=next_action.text,
                    log=log,
                    metrics=metrics,
                    turn_started=turn_started,
                    tool_request=last_request,
                    tool_result=last_result,
                )

            log.append(
                LogEntry(
                    "model_tool_request",
                    {"name": next_action.name, "arguments": next_action.arguments},
                )
            )
            if not self._may_continue(
                rejections=rejections, model_calls=model_calls, deadline=deadline
            ):
                log.append(LogEntry("continuation_budget_exhausted", {"model_calls": model_calls}))
                return _finish_turn_result(
                    final_text=_observation_exhausted_text(action, structured_result),
                    log=log,
                    metrics=metrics,
                    turn_started=turn_started,
                    tool_request=last_request,
                    tool_result=last_result,
                )
            action = next_action

    def _may_continue(
        self,
        *,
        rejections: int,
        model_calls: int,
        deadline: Deadline,
    ) -> bool:
        """Whether another model round-trip is permitted this turn.

        Bounds the whole chain: rejection retries and multi-tool continuation
        share one budget, and the adapter's own repair calls are counted because
        ``model_call_count`` includes them.
        """
        if deadline.expired:
            return False
        if rejections > self._max_rejections:
            return False
        return self._observed_model_calls(model_calls) < self._max_model_calls

    def _observed_model_calls(self, loop_count: int) -> int:
        """Total model calls, including adapter repair retries.

        The adapter tracks every generation it performs, so its counter is the
        authoritative figure when present; the loop's own count is a floor.
        """
        diagnostics = _model_diagnostics(self._model)
        if diagnostics is not None and diagnostics.model_call_count:
            return max(diagnostics.model_call_count, loop_count)
        return loop_count

    def _resume(
        self,
        user_text: str,
        observation: ToolObservation,
        *,
        log: list[LogEntry],
        status_callback: Callable[[str], None] | None,
        message: str,
    ) -> ModelAction:
        log.append(
            LogEntry(
                "tool_rejection_observation",
                {"name": observation.request.name, "delivered": True},
            )
        )
        _status(status_callback, message)
        return self._model.respond_to_observation(user_text, observation)

    @staticmethod
    def _drain_reminders(pending: list[str]) -> dict[str, Any]:
        if not pending:
            return {}
        items = list(pending)
        pending.clear()
        return {"repeat_reminder": " ".join(items)}

    def _classify_timeout(
        self, tool_name: str, structured_result: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Tag a result the execution layer reported as timed out."""
        status = structured_result.get("status")
        if not isinstance(status, str):
            return None
        elapsed = structured_result.get("duration")
        return self._timeouts.classify(
            tool_name=tool_name,
            status=status,
            elapsed_seconds=float(elapsed) if isinstance(elapsed, (int, float)) else 0.0,
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
        """Validate a tool call, with one authoritative schema implementation.

        Invariant: ``tool_contracts.TOOL_REQUEST_SCHEMAS`` is the **model-facing**
        contract. The model is only ever offered tools from that registry --
        ``_select_tools`` renders them for Ollama via ``build_openai_tools``, and
        ``render_tool_contracts`` renders the same set into the system prompt.
        Validating proposals against it therefore checks the model against
        exactly what it was told, which is why static validation is intentional
        rather than a narrowing.

        Registry *membership* is checked here because whether a tool is actually
        mounted is dynamic: it depends on which MCP servers are attached. A tool
        that a server exposes but the registry omits is not proposable in the
        first place, since the model is never told about it.
        """

        if request.name not in tools:
            raise ValueError(f"Unknown MCP tool requested: {request.name}")
        validate_tool_arguments(request.name, request.arguments)

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


def _normalize_mcp_servers(mcp_server: Any) -> tuple[Any, ...]:
    if mcp_server is None:
        return ()
    if isinstance(mcp_server, Sequence) and not isinstance(mcp_server, (str, bytes, bytearray)):
        return tuple(server for server in mcp_server if server is not None)
    return (mcp_server,)


def _status(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _rejection_exhausted_text(request: ToolRequest, reason: str) -> str:
    """Honest final text when a rejected tool call ran out of correction budget."""

    return (
        f"I could not form a valid call to `{request.name}` before running out of "
        f"correction attempts. The last rejection was: {reason}"
    )


def _observation_exhausted_text(request: ToolRequest, result: dict[str, Any]) -> str:
    """Honest final text when a multi-tool chain hit the per-turn call ceiling."""

    return (
        f"I stopped after `{request.name}` because this turn reached its limit on "
        f"model calls without a final answer. The last result was: {result}"
    )


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
