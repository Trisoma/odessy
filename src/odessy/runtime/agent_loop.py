from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from mcp import Client

from odessy.execution.escalation import EscalationApproval, EscalationApprover, EscalationDeniedError
from odessy.tools.external_tools import NamespacedServer, public_name
from odessy.authority.policy import OdessyPolicy
from odessy.runtime.repeat_guard import RepeatGuard, build_guard
from odessy.tools.tool_contracts import validate_tool_arguments
from odessy.runtime.tool_timeout import Deadline, ToolTimeoutPolicy
from odessy.runtime.types import (
    AgentTurnMetrics,
    AgentTurnResult,
    ConversationContext,
    FinalResponse,
    LogEntry,
    ModelTurnDiagnostics,
    ModelAction,
    ToolObservation,
    ToolRequest,
    ToolBatch,
    ToolCall,
)


@dataclass(frozen=True, slots=True)
class ExecutionTarget:
    """A server this deployment has explicitly authorized for execution.

    Authorization belongs to the **target**, not to the agent's construction. An agent
    that reads "I was handed this" as "this is authorized" holds no authorization at all,
    only a tautology -- and anything that can influence construction (an environment
    variable, a plugin, a discovery step) then influences authorization.

    Because this is a distinct type that nothing produces incidentally, holding one *is*
    the authorization: a server that is merely reachable is not executable.

    Nothing here establishes trust. There is no identity, no key, no persistence, no
    revocation. It marks the seat that pairing will occupy, and its value is that the
    default is no longer "trusted because supplied".
    """

    server: Any


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

#: Instruction carried by the observation of a tool call that ran and failed.
_TOOL_ERROR_INSTRUCTION = (
    "The tool ran and failed. Correct the call (for example the path, which is "
    "relative to the workspace) and try again, or answer briefly with what went wrong."
)

#: Characters of a tool's error message kept in the observation.
_TOOL_ERROR_CHARS = 1000


def _is_error_result(result: Any) -> bool:
    return bool(getattr(result, "is_error", getattr(result, "isError", False)))


def _error_text(result: Any) -> str:
    """The text a failed MCP call returned, for the model to read."""

    parts = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    message = "\n".join(parts) or "the tool reported an error without a message"
    return message[:_TOOL_ERROR_CHARS]


class OdessyAgent:
    """Single-turn Odessy runtime loop with model-independent tool execution."""

    def __init__(
        self,
        *,
        model: ModelAdapter,
        policy: OdessyPolicy,
        mcp_server: Any,
        confirm: Callable[[Any, ToolRequest], bool] | bool = False,
        approver: EscalationApprover | None = None,
        max_rejections: int = 2,
        deadline_seconds: float | None = None,
        max_model_calls: int = 4,
        guard: RepeatGuard | None = None,
        timeouts: ToolTimeoutPolicy | None = None,
        tool_concurrency: int = 1,
    ) -> None:
        if not 1 <= tool_concurrency <= 8:
            raise ValueError("tool_concurrency must be from 1 to 8")
        self._tool_concurrency = tool_concurrency
        self._model = model
        self._policy = policy
        self._mcp_servers, self._authorized_targets = _split_mcp_servers(mcp_server)
        self._confirm = confirm
        #: The human approval channel for a *sandbox escalation* request. Distinct from
        #: `confirm`, which answers the authority question "may this action run at
        #: all?". This one answers only "did the human approve the wider sandbox mode?".
        #:
        #: It decides nothing else: it does not judge safety and does not pick a
        #: provider. A missing approver fails closed, so an escalation that cannot be
        #: asked for is an escalation that cannot be granted.
        self._approver = approver
        #: Bounded retries on pre-execution (schema/argument) rejection. Authority
        #: decisions never retry -- see run_turn.
        self._max_rejections = max(0, max_rejections)
        #: Optional shared wall-clock budget for one turn.
        #:
        #: INTENTIONALLY DEFAULTED TO None. This is a deliberate decision, not
        #: an oversight. `None` becomes an infinite budget, so the deadline never
        #: expires and `max_model_calls` is the only default protection.
        #:
        #: Why no default: a wrong value silently truncates legitimate turns, and
        #: no measured distribution of turn durations exists to pick one from.
        #:
        #: This is NOT the mechanism that bounds model generation. That job
        #: belongs to ODESSY_MODEL_PROGRESS_TIMEOUT, which is an inter-chunk
        #: progress timeout: its timer resets on every streaming chunk received,
        #: so it limits the maximum silence between chunks rather than total
        #: generation duration. A healthy generation may therefore run
        #: indefinitely as long as chunks keep arriving.
        #:
        #: (An earlier revision of this comment justified the absent default by
        #: a 120 s whole-request HTTP timeout. That timeout no longer exists;
        #: it was replaced by the per-chunk progress timeout described above.)
        self._deadline_seconds = deadline_seconds
        #: Hard ceiling on model generations per turn, covering rejection retries,
        #: multi-tool continuation, and the adapter's own repair calls.
        self._max_model_calls = max(1, max_model_calls)
        self._guard_template = guard
        #: Serialises model generation on this agent instance.
        #:
        #: The adapters are synchronous and stream over ``urllib``, so calling them
        #: inline blocks the event loop. Text mode never noticed; a voice session
        #: does, because a blocked loop cannot read the microphone and would not hear
        #: the wake phrase during a generation. Generation therefore runs in a worker
        #: thread, and the lock keeps exactly one generation in flight per adapter:
        #: the adapters hold per-turn state (``_turn_observations``, ``_messages``)
        #: that a second concurrent entry would corrupt.
        self._model_lock = asyncio.Lock()
        self._timeouts = timeouts or ToolTimeoutPolicy()

    async def _generate(self, call: Callable[[], ModelAction]) -> ModelAction:
        """Run one synchronous model call without blocking the event loop."""

        async with self._model_lock:
            return await asyncio.to_thread(call)

    async def _propose(self, user_text: str, context: ConversationContext | None) -> ModelAction:
        """Propose with history only when there is history.

        A caller that passes nothing gets exactly the call shape text mode and the
        evaluation suite already use, so no adapter has to grow a parameter to keep
        working. An adapter that *does* receive history must render it: passing it
        where it is unsupported raises TypeError rather than silently dropping it.
        """

        if context is None or not context.turns:
            return await self._generate(lambda: self._model.propose(user_text))
        return await self._generate(lambda: self._model.propose(user_text, context=context))

    @staticmethod
    def _stop_requested(stop_event: asyncio.Event | None) -> bool:
        return stop_event is not None and stop_event.is_set()

    async def run_turn(
        self,
        user_text: str,
        *,
        status_callback: Callable[[str], None] | None = None,
        stop_event: asyncio.Event | None = None,
        context: ConversationContext | None = None,
    ) -> AgentTurnResult:
        turn_started = time.perf_counter()
        metrics = AgentTurnMetrics()
        log: list[LogEntry] = [LogEntry("user_input", {"text": user_text})]
        if self._stop_requested(stop_event):
            return _finish_turn_result(
                final_text="Stopped before generation.",
                log=log,
                metrics=metrics,
                turn_started=turn_started,
            )
        _status(status_callback, "Generating action proposal")
        proposal_started = time.perf_counter()
        action = await self._propose(user_text, context)
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
        observations: list[ToolObservation] = []
        dispatched = 0
        batch_number = 0
        while True:
            if stop_event is not None and stop_event.is_set():
                if isinstance(action, ToolBatch):
                    skipped = [ToolObservation(c.request, {"status": "skipped", "reason": "stopped by user"}, c.call_id) for c in action.calls]
                    observations.extend(skipped)
                    finalizer = getattr(self._model, "finalize_observations", None)
                    if callable(finalizer):
                        finalizer(skipped)
                return _finish_turn_result(final_text="Stopped. No further tools were dispatched.", log=log, metrics=metrics, turn_started=turn_started, tool_request=last_request, tool_result=last_result, tool_observations=observations)
            if isinstance(action, ToolBatch):
                from odessy.runtime.scheduler import schedule_calls, is_concurrency_safe
                if not callable(getattr(self._model, "respond_to_observations", None)):
                    raise TypeError("A model emitting ToolBatch must implement respond_to_observations")
                batch_number += 1
                batch_id = f"batch-{batch_number}"
                failure = None
                reminders = {}
                for call in action.calls:
                    if self._policy.evaluate(call.request).action == "deny":
                        failure = "Policy denied request: " + self._policy.evaluate(call.request).reason
                        break
                if failure is not None:
                    batch_observations = [ToolObservation(c.request, {"status": "denied", "reason": failure}, c.call_id) for c in action.calls]
                    stopped = failure
                else:
                    if tool_registry is None:
                        tool_registry = await self._collect_tool_registry()
                    invalid = None
                    for call in action.calls:
                        try:
                            self._validate_tool_request(call.request, tool_registry)
                        except (ValueError, TypeError) as exc:
                            invalid = str(exc)
                            break
                    if invalid:
                        rejections += 1
                        batch_observations = [ToolObservation(c.request, {"status": "rejected", "reason": invalid}, c.call_id) for c in action.calls]
                        stopped = None
                    elif dispatched + len(action.calls) > 64:
                        batch_observations = [ToolObservation(c.request, {"status": "skipped", "reason": "64-call turn limit reached"}, c.call_id) for c in action.calls]
                        stopped = "64-call turn limit reached"
                    else:
                        def safe(call):
                            request = call.request
                            decision = self._policy.evaluate(request)
                            return is_concurrency_safe(request) and decision.allowed and self._policy.requirement_for(request, decision) is None
                        async def prepare(call):
                            nonlocal dispatched
                            reminder = guard.observe(call.request.name, call.request.arguments)
                            log.append(LogEntry("model_tool_request", {"name": call.request.name, "arguments": call.request.arguments, "call_id": call.call_id, "batch_id": batch_id}))
                            if reminder:
                                reminders[call.call_id] = reminder
                                log.append(LogEntry("repeat_tool_reminder", {"name": call.request.name}))
                            try:
                                args = await self._prepare_call(call.request, tool_registry, log, metrics)
                            except EscalationDeniedError as exc:
                                raise PermissionError(str(exc)) from exc
                            dispatched += 1
                            return args
                        async def dispatch(call, args):
                            started = time.perf_counter()
                            try:
                                result = await self._call_tool(call.request.name, args, tool_registry)
                                structured = {"status": "error", "error": _error_text(result)} if _is_error_result(result) else self._extract_structured_result(result)
                            except Exception as exc:
                                structured = {"status": "error", "error": str(exc)}
                            return {**structured, "_dispatch_timing": {"started": started, "completed": time.perf_counter()}}
                        batch_observations, stopped = await schedule_calls(action, limit=self._tool_concurrency, safe=safe, prepare=prepare, dispatch=dispatch, stop=stop_event)
                for index, observation in enumerate(batch_observations):
                    if observation.call_id in reminders:
                        observation.result["repeat_reminder"] = reminders[observation.call_id]
                    timeout_info = self._classify_timeout(observation.request.name, observation.result)
                    if timeout_info is not None:
                        log.append(LogEntry("tool_timeout", {"name": observation.request.name, "call_id": observation.call_id, **timeout_info}))
                    timing = observation.result.pop("_dispatch_timing", None)
                    if timing:
                        log.append(LogEntry("mcp_tool_called", {"name": observation.request.name, "call_id": observation.call_id, "batch_id": batch_id, "index": index, **timing}))
                        last_request, last_result = observation.request, observation.result
                        _add_ms(metrics, "mcp_execution_ms", (timing["completed"]-timing["started"])*1000)
                    log.append(LogEntry("tool_observation", {"name": observation.request.name, "call_id": observation.call_id, "batch_id": batch_id, "index": index, "result": observation.result}))
                observations.extend(batch_observations)
                if stopped:
                    event = "continuation_budget_exhausted" if "limit" in stopped else "batch_stopped"
                    log.append(LogEntry(event, {"reason": stopped}))
                if stopped or not self._may_continue(rejections=rejections, model_calls=model_calls, deadline=deadline):
                    if not stopped:
                        log.append(LogEntry("continuation_budget_exhausted", {"model_calls": model_calls}))
                    finalizer = getattr(self._model, "finalize_observations", None)
                    if callable(finalizer):
                        finalizer(batch_observations)
                    return _finish_turn_result(final_text=stopped or "Tool continuation budget exhausted.", log=log, metrics=metrics, turn_started=turn_started, tool_request=last_request, tool_result=last_result, tool_observations=observations)
                action = await self._generate(
                    lambda: self._model.respond_to_observations(user_text, batch_observations)
                )
                model_calls += 1
                _merge_model_diagnostics(metrics, _model_diagnostics(self._model))
                if isinstance(action, FinalResponse):
                    log.append(LogEntry("model_final_response", {"text": action.text}))
                    return _finish_turn_result(final_text=action.text, log=log, metrics=metrics, turn_started=turn_started, tool_request=last_request, tool_result=last_result, tool_observations=observations)
                continue
            if dispatched >= 64:
                return _finish_turn_result(final_text="64-call turn limit reached.", log=log, metrics=metrics, turn_started=turn_started, tool_request=last_request, tool_result=last_result, tool_observations=observations)
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
                        tool_observations=observations,
                    )
                next_action = await self._resume(
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
                        tool_observations=observations,
                    )
                action = next_action
                continue

            log.append(LogEntry("schema_validated", {"name": action.name}))

            call_arguments = await self._prepare_call(action, tool_registry, log, metrics)

            _status(status_callback, "Running tool")
            mcp_started = time.perf_counter()
            dispatched += 1
            if self._stop_requested(stop_event):
                # The stop check sits immediately before dispatch, not only at the top
                # of the loop: a confirmation or a repair can take long enough for the
                # user to cancel in between, and a cancelled turn must not act.
                return _finish_turn_result(
                    final_text="Stopped before tool dispatch.",
                    log=log,
                    metrics=metrics,
                    turn_started=turn_started,
                    tool_request=last_request,
                    tool_result=last_result,
                    tool_observations=observations,
                )
            result = await self._call_tool(action.name, call_arguments, tool_registry)
            log.append(LogEntry("mcp_tool_called", {"name": action.name}))

            if _is_error_result(result):
                # A failed call (a wrong path, a missing file) is something the model
                # can correct, like a rejection: it becomes an observation, and the next
                # action goes through validation and policy as usual. Ending the turn
                # here meant one mistyped path lost the whole request. The model-call
                # ceiling still bounds the retries.
                structured_result = {
                    "status": "error",
                    "error": _error_text(result),
                    "instruction": _TOOL_ERROR_INSTRUCTION,
                }
                log.append(LogEntry("tool_error", {"name": action.name, "error": structured_result["error"]}))
            else:
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
            observations.append(observation)
            log.append(
                LogEntry(
                    "tool_observation",
                    {"name": action.name, "result": structured_result},
                )
            )

            _status(status_callback, "Generating response")
            final_started = time.perf_counter()
            next_action = await self._generate(
                lambda: self._model.respond_to_observation(user_text, observation)
            )
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
                    tool_observations=observations,
                )

            if isinstance(next_action, ToolRequest):
                log.append(LogEntry("model_tool_request", {"name": next_action.name, "arguments": next_action.arguments}))
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
                    tool_observations=observations,
                )
            action = next_action

    async def _prepare_call(self, action, tool_registry, log, metrics):
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

        self._assert_execution_target(action.name, tool_registry)

        # Isolation is resolved HERE, on the authority side, before anything
        # executes. The granted mode travels with the call; the model never sets
        # it, because a model-settable mode would make escalation bypassable in a
        # single argument.
        call_arguments = dict(action.arguments)
        if self._policy.requirement_for(action, decision) is not None:
            resolved_mode = await self._policy.resolve_execution_mode(
                action,
                decision,
                approval=EscalationApproval(
                    approver=self._approver,
                    agent=self,
                    call_id=uuid.uuid4().hex[:12],
                    tool_name=action.name,
                ),
            )
            # `sandbox_permissions` and `justification` are the escalating
            # requester's vocabulary and stay on this side. Only the GRANTED mode
            # crosses to the executor, so the executor never re-decides and never
            # even sees what was asked for.
            call_arguments.pop("sandbox_permissions", None)
            call_arguments.pop("justification", None)
            call_arguments["mode"] = resolved_mode
            # The requirement travels WITH the mode. Sending only the mode would
            # leave the executor to infer how strict this side is, and inferring
            # permissively would be the executor weakening the authority layer's
            # requirement -- a `required` demand silently satisfied by a `partial`
            # backend. They are a pair, like the escalation arguments themselves.
            call_arguments["isolation"] = self._policy.isolation
            log.append(
                LogEntry(
                    "execution_mode_resolved",
                    {"name": action.name, "mode": resolved_mode},
                )
            )

        return call_arguments

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

    async def _resume(
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
        return await self._generate(lambda: self._model.respond_to_observation(user_text, observation))

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

    def _assert_execution_target(
        self, name: str, registry: dict[str, tuple[Any, Any]]
    ) -> None:
        """Refuse to act on a server that is not a declared execution target.

        The action is refused in full, not merely sent unconfined: an undeclared target
        is not an execution target at all, and running there without a boundary would be
        the same as treating reachability as trust.
        """

        entry = registry.get(name)
        if entry is None:
            # An unknown tool is `_call_tool`'s error to report, not this one's.
            return
        _, server = entry
        if not any(server is target for target in self._authorized_targets):
            raise PermissionError(
                f"{name} would act on a server that is not a declared execution target; "
                "wrap the server in ExecutionTarget at the composition root to authorize it"
            )

    async def _collect_tool_registry(self) -> dict[str, tuple[Any, Any]]:
        if not self._mcp_servers:
            return {}

        registry: dict[str, tuple[Any, Any]] = {}
        for server in self._mcp_servers:
            namespaced = isinstance(server, NamespacedServer)
            async with Client(server.connection() if namespaced else server) as client:
                tools = await client.list_tools()
            available_tools = getattr(tools, "tools", tools)
            for tool in available_tools:
                # A configured third-party server's tools are offered as
                # "<server>.<tool>": they cannot shadow a built-in or each other, and the
                # stored tool keeps its own name for the call.
                name = public_name(server.prefix, tool.name) if namespaced else tool.name
                registry.setdefault(name, (tool, server))
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
        tool, server = entry
        if isinstance(server, NamespacedServer):
            async with Client(server.connection()) as client:
                return await asyncio.wait_for(
                    client.call_tool(tool.name, arguments), timeout=server.config.timeout
                )
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
        if isinstance(structured, dict):
            return structured
        if structured is not None:
            return {"status": "ok", "result": structured}
        # Most third-party tools return text content only. Odessy's own servers always
        # return structured content, so this path serves configured external servers.
        texts = [
            item.text
            for item in getattr(result, "content", None) or []
            if isinstance(getattr(item, "text", None), str)
        ]
        if texts:
            return {"status": "ok", "content": "\n".join(texts)}
        raise TypeError("MCP tool result included neither structured nor text content")


def _split_mcp_servers(mcp_server: Any) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """Return the servers to talk to, and the subset authorized for execution.

    Both are derived from one input so they cannot drift: authorization is read off the
    type, never off a parallel list someone has to remember to update.
    """

    raw = _normalize_mcp_servers(mcp_server)
    authorized = tuple(item.server for item in raw if isinstance(item, ExecutionTarget))
    servers = tuple(item.server if isinstance(item, ExecutionTarget) else item for item in raw)
    return servers, authorized


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
    tool_observations: list[ToolObservation] | None = None,
) -> AgentTurnResult:
    metrics.agent_turn_total_ms = _elapsed_ms(turn_started)
    log.append(LogEntry("agent_turn_metrics", asdict(metrics)))
    return AgentTurnResult(
        final_text=final_text,
        log=log,
        tool_request=tool_request,
        tool_result=tool_result,
        metrics=metrics,
        tool_observations=tool_observations or [],
    )
