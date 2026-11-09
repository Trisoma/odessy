"""Bind a Harness permission request to one exact Odessy approval record.

The pinned ACP server asks with ``session/request_permission`` and carries only
``{sessionId, toolCall: {toolCallId}, options}`` (:mod:`approvals` is the only consumer).
That is *not* enough to show a person what they are approving, and it is not an Odessy
approval identity. Two things are therefore combined before anything is asked:

* the live ``session/update`` ``tool_call`` frame, which is the runtime's own committed
  record of the call — its tool name (``title``) and its parsed arguments (``rawInput``);
* the session's own identity (runtime version and artifact, ACP session id, Odessy
  session id, turn number, JSON-RPC request id, ``toolCallId``).

The result is projected through the *existing* approval builder
(:func:`odessy.authority.action_approval.build_action_approval`), so the person sees the
same operation, arguments, resolved paths, workspace, execution target, scopes and
fingerprint shape that every other Odessy approval binds. A generic tool name is never
enough: without a bindable call identity the request is answered ``cancelled`` and the
turn is refused, because an approval nobody can describe is not consent.

What this module does **not** do:

* it does not execute, retry or sandbox anything — it only asks and answers;
* it never answers ``selected`` for an option kind it does not understand, so a durable
  grant (``allow_always``) can never be taken on someone's behalf;
* it has no notion of parent/child session ancestry. Ownership is "this connection saw
  this call in this turn", not "some durable session once belonged to the same tree";
* it does not treat a question as an approval. Question methods are counted and refused;
  a permission request that offers anything other than one one-shot allow and one
  one-shot reject is malformed and is refused.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from odessy.authority.action_approval import ActionApprovalPlan, build_action_approval
from odessy.authority.policy import OdessyPolicy
from odessy.runtime.types import PolicyDecision, ToolRequest

PERMISSION_METHOD = "session/request_permission"

#: One-shot ACP option kinds this bridge understands. Anything else — a durable grant,
#: a free-form option, an unknown vendor kind — is never selected.
ALLOW_ONCE = "allow_once"
REJECT_ONCE = "reject_once"

#: Request methods that ask a person a *question* rather than authorize an operation.
#: None of them exists on the pinned ACP server; the set exists so a question-shaped
#: request is refused as a question (and never routed to the approval broker), instead of
#: being answered as if it were consent.
QUESTION_METHODS = frozenset({
    "elicitation/create",
    "session/request_question",
    "user-questions/request",
    "ask_user_question",
})

#: A bound answer is only written while its turn is still the live one.
CANCELLED_RESPONSE: dict[str, Any] = {
    "result": {"outcome": {"outcome": "cancelled"}}
}


class ApprovalBindError(ValueError):
    """A permission request cannot be bound to one exact operation.

    Every instance of this class ends in ``cancelled``; none of them can authorize.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def allowed_response(option_id: str) -> dict[str, Any]:
    return {"result": {"outcome": {"outcome": "selected", "optionId": option_id}}}


def rejected_response(option_id: str) -> dict[str, Any]:
    return {"result": {"outcome": {"outcome": "selected", "optionId": option_id}}}


def unsupported_response(method: str) -> dict[str, Any]:
    return {
        "error": {
            "code": -32601,
            "message": f"Odessy does not handle {method}; it is not an approval",
        }
    }


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass
class ToolCallRecord:
    """What the runtime itself committed about one tool call, from ``session/update``."""

    session_id: str
    turn: int
    tool_call_id: str
    name: str
    arguments: dict[str, Any]
    status: str = "in_progress"
    #: The wire value as received. A non-object value cannot be shown faithfully as an
    #: operation, so the request is refused instead of being approximated.
    raw_arguments: Any = None

    def binding(self) -> str:
        return _canonical({"session": self.session_id, "turn": self.turn,
                           "call": self.tool_call_id, "name": self.name,
                           "arguments": self.arguments})


@dataclass
class BoundRequest:
    """One permission request, bound to the operation the person was shown."""

    request_id: Any
    record: ToolCallRecord
    allow_option: str
    reject_option: str
    details: dict[str, Any]
    plan: ActionApprovalPlan
    request: ToolRequest
    decision: PolicyDecision
    policy: OdessyPolicy
    target: dict[str, Any]
    stop_epoch: int
    state: str = "pending"


@dataclass
class ApprovalStats:
    """Counters a caller may inspect; never contains arguments or answers."""

    bound: int = 0
    allowed: int = 0
    rejected: int = 0
    cancelled: int = 0
    unbindable: int = 0
    unsupported: int = 0
    questions: int = 0
    duplicate: int = 0
    #: An allow the human gave that was downgraded to ``cancelled`` at the wire because the
    #: session was stopped or the operation ended before the write could be delivered.
    retracted: int = 0
    #: A request that asked to widen the sandbox. Never bound: the pinned runtime decides
    #: its mode per process, so an escalation cannot be carried here (prompt 08).
    escalation_refused: int = 0

    def public(self) -> dict[str, int]:
        return dict(self.__dict__)


#: The argument a widening request carries. ``odessy.authority.policy`` validates the same
#: name for the Python side; here it marks a request this bridge must not answer as an
#: ordinary approval.
ESCALATION_ARGUMENT = "sandbox_permissions"


def escalation_target(arguments: Mapping[str, Any]) -> str | None:
    """The mode a permission request is asking to widen to, when it asks at all.

    A request that names ``sandbox_permissions`` is not an ordinary approval: it asks to
    re-run the call outside the confinement the session launched with. Returning the target
    lets the caller refuse it explicitly instead of binding it as a normal allow.
    """

    if ESCALATION_ARGUMENT not in arguments:
        return None
    value = arguments.get(ESCALATION_ARGUMENT)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "<malformed>"


def _once_options(options: Any) -> tuple[str, str]:
    """Exactly one one-shot allow and one one-shot reject, or the request is malformed."""

    if not isinstance(options, (list, tuple)) or not options:
        raise ApprovalBindError("the request offered no options")
    allow, reject = [], []
    for option in options:
        if not isinstance(option, Mapping):
            raise ApprovalBindError("an option was not an object")
        kind = option.get("kind")
        option_id = option.get("optionId")
        if kind not in (ALLOW_ONCE, REJECT_ONCE):
            # A durable grant is a different consent. It is never selected, and its
            # presence does not make the request unusable.
            continue
        if not isinstance(option_id, str) or not option_id:
            raise ApprovalBindError(f"the {kind} option has no usable id")
        (allow if kind == ALLOW_ONCE else reject).append(option_id)
    if len(allow) != 1 or len(reject) != 1:
        raise ApprovalBindError(
            "the request must offer exactly one one-shot allow and one one-shot reject"
        )
    if allow[0] == reject[0]:
        # One id cannot mean both answers: on the wire a denial and a grant would be
        # indistinguishable, and the runtime would read whichever it checks first.
        raise ApprovalBindError("the allow and reject options share one id")
    return allow[0], reject[0]


class HarnessApprovalBridge:
    """Ask the session's own broker to decide one Harness permission request.

    ``approver`` is the existing application broker call
    ``(details) -> (approved, reason)``; this class owns identity, binding, one-shot
    semantics and the ACP translation, and nothing else.
    """

    def __init__(self, *, workspace: Path, identity: Callable[[], Mapping[str, Any]],
                 approver: Callable[[dict[str, Any]], Any],
                 epoch: Callable[[], int] | None = None,
                 is_closed: Callable[[], bool] | None = None,
                 escalation: str = "ask",
                 sandbox_mode: str = "workspace-write"):
        self.workspace = Path(workspace)
        self._identity = identity
        self._approver = approver
        self._epoch = epoch or (lambda: 0)
        self._is_closed = is_closed or (lambda: False)
        #: ``sandbox.toml``'s escalation setting for this target. ``never`` means a widening
        #: request is refused without asking a person.
        self._escalation = escalation
        #: The mode the session launched with. A request that asks for that mode or a
        #: narrower one is an ordinary approval (it grants nothing new); a request that asks
        #: for a wider one cannot be honoured here and is refused.
        self._sandbox_mode = sandbox_mode
        self._lock = threading.RLock()
        self._turn: int | None = None
        self._session_id = ""
        self._calls: dict[str, ToolCallRecord] = {}
        self._decided: set[str] = set()
        self._live: dict[Any, BoundRequest] = {}
        self._failures: list[tuple[str, str]] = []
        self._shutdown = threading.Event()
        self.stats = ApprovalStats()

    # ------------------------------------------------------------------ turn scope

    def begin_turn(self, turn: int, *, session_id: str | None = None) -> int:
        """Own a new turn. A stale call from a previous turn cannot be bound after this."""
        with self._lock:
            self._turn = int(turn)
            if session_id is not None:
                self._session_id = str(session_id)
            self._calls = {}
            self._decided = set()
            return self._turn

    def end_turn(self) -> None:
        """The turn is over: no new bind, and a late answer can no longer be allowed."""
        with self._lock:
            self._turn = None
            self._live = {}

    def close(self) -> None:
        self._shutdown.set()
        with self._lock:
            self._turn = None
            self._live = {}

    @property
    def closed(self) -> bool:
        return self._shutdown.is_set()

    def observe(self, notification: Mapping[str, Any]) -> None:
        """Record the runtime's own committed ``tool_call`` identity. Thread-safe."""
        try:
            if notification.get("method") != "session/update":
                return
            params = notification.get("params") or {}
            update = params.get("update") or {}
            kind = update.get("sessionUpdate")
            if kind not in ("tool_call", "tool_call_update"):
                return
            call_id = update.get("toolCallId")
            if not isinstance(call_id, str) or not call_id:
                return
            with self._lock:
                if self._turn is None:
                    return  # a frame that arrives outside a turn belongs to no operation
                record = self._calls.get(call_id)
                if kind == "tool_call":
                    raw = update.get("rawInput")
                    self._calls[call_id] = ToolCallRecord(
                        session_id=self._session_id,
                        turn=self._turn,
                        tool_call_id=call_id,
                        name=str(update.get("title") or ""),
                        arguments=dict(raw) if isinstance(raw, Mapping) else {},
                        status=str(update.get("status") or "in_progress"),
                        raw_arguments=raw,
                    )
                elif record is not None:
                    record.status = str(update.get("status") or record.status)
        except Exception:  # noqa: BLE001 - an observer must never fail the turn
            pass

    # --------------------------------------------------------------------- bind

    def bind(self, request_id: Any, params: Any) -> BoundRequest:
        """Validate one permission request and bind it to the operation on screen."""
        if not isinstance(params, Mapping):
            raise ApprovalBindError("the request params were not an object")
        with self._lock:
            if self._turn is None:
                raise ApprovalBindError("no turn is running")
            session_id = params.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise ApprovalBindError("the request named no session")
            if session_id != self._session_id:
                # Not a parent/child heuristic: another session id is another owner.
                raise ApprovalBindError("the request names another session")
            tool_call = params.get("toolCall")
            if not isinstance(tool_call, Mapping):
                raise ApprovalBindError("the request carried no tool call")
            call_id = tool_call.get("toolCallId")
            if not isinstance(call_id, str) or not call_id:
                raise ApprovalBindError("the request carried no tool call id")
            record = self._calls.get(call_id)
            if record is None:
                raise ApprovalBindError("no observed tool call matches the request")
            if record.status in ("completed", "failed"):
                raise ApprovalBindError("the tool call already ended")
            if call_id in self._decided:
                self.stats.duplicate += 1
                raise ApprovalBindError("this tool call was already decided")
            if any(item.record.tool_call_id == call_id for item in self._live.values()):
                self.stats.duplicate += 1
                raise ApprovalBindError("a decision for this tool call is already pending")
            allow_id, reject_id = _once_options(params.get("options"))
            turn = record.turn
            arguments = dict(record.arguments)
            name = record.name
        if not name:
            raise ApprovalBindError("the observed tool call named no tool")
        raw_arguments = getattr(record, "raw_arguments", arguments)
        if not isinstance(raw_arguments, Mapping):
            # A faithful review view of an unparseable argument blob is not something a
            # person can approve: bind only structured arguments.
            raise ApprovalBindError("the observed tool call carried non-object arguments")
        widening = escalation_target(raw_arguments)
        if widening is not None:
            refusal = self._escalation_refusal(widening)
            if refusal is not None:
                self.stats.escalation_refused += 1
                raise ApprovalBindError(refusal)

        identity = dict(self._identity() or {})
        target = {
            "host": "local",
            "runtime": "harness",
            "runtime_version": identity.get("version"),
            "runtime_artifact": identity.get("artifact"),
            "acp_session": self._session_id,
            "odessy_session": identity.get("odessy_session"),
            "request_id": request_id,
            "tool_call_id": call_id,
            "turn": turn,
            "workspace": str(self.workspace),
        }
        target = {key: value for key, value in target.items() if value is not None}
        justification = arguments.get("justification")
        reason = (justification.strip() if isinstance(justification, str)
                  and justification.strip()
                  else f"the runtime requested permission to run {name}")
        # A Harness permission prompt is an escalation: the runtime asks only when its
        # own policy refused to proceed. Treating it as high risk keeps the existing
        # rule that a spoken phrase is an intent, not an identity, for escalations.
        decision = PolicyDecision("confirm", reason, "high", rule="harness_permission")
        policy = OdessyPolicy(self.workspace)
        plan = build_action_approval(
            ToolRequest(name, arguments), decision, policy,
            call_id=call_id, execution_target=target,
        )
        details = plan.public_dict()
        details.update({
            "reason": reason,
            "source": "harness",
            "surface": "runtime permission request",
            "workspace": str(self.workspace),
            # What the runtime offered, not what it will get: only one-shot kinds exist.
            "requested_authority": [ALLOW_ONCE],
            "runtime_identity": {
                "runtime": "harness",
                "version": identity.get("version"),
                "artifact": identity.get("artifact"),
            },
            "session_identity": {
                "acp_session": self._session_id,
                "odessy_session": identity.get("odessy_session"),
            },
            "request_identity": {
                "request_id": request_id,
                "method": PERMISSION_METHOD,
                "tool_call_id": call_id,
                "turn": turn,
            },
        })
        bound = BoundRequest(
            request_id=request_id, record=record, allow_option=allow_id,
            reject_option=reject_id, details=details, plan=plan,
            request=ToolRequest(name, arguments), decision=decision, policy=policy,
            target=target, stop_epoch=int(self._epoch() or 0),
        )
        with self._lock:
            # A Stop may have landed between the checks above and here.
            if int(self._epoch() or 0) != bound.stop_epoch or self._turn != turn:
                raise ApprovalBindError("the turn changed while the request was bound")
            self._live[request_id] = bound
            self.stats.bound += 1
        return bound

    # -------------------------------------------------------------------- answer

    def _escalation_refusal(self, target: str) -> str | None:
        """Why a widening request cannot be answered here, or None when it is not widening.

        A request that names ``sandbox_permissions`` at or below the session's own mode asks
        for nothing new, so it is an ordinary one-shot approval. A request for a *wider* mode
        cannot be honoured: the pinned runtime resolves ``sandbox-policy.mode`` once per
        process and offers no session option for it (``probe_sandbox_vocabulary.py``:
        ``session/new`` returns only ``model`` and ``reasoning_effort``). Answering
        ``allow_once`` there would be a false statement about the boundary.
        """

        rank = {"read-only": 0, "workspace-write": 1, "danger-full-access": 2}
        wanted = rank.get(target)
        if wanted is None:
            return (
                f"the request named an unknown sandbox mode {target!r}; a widening request "
                "cannot be reviewed when its target cannot be ranked"
            )
        if wanted <= rank.get(self._sandbox_mode, 1):
            return None
        if self._escalation == "never":
            return (
                "the escalation setting for this target is 'never', so a widening request is "
                "refused rather than shown to a person"
            )
        return (
            f"an escalation to {target!r} cannot be carried by this bridge: the pinned "
            "runtime resolves the sandbox mode once per process, so an approval could not "
            "be honoured"
        )

    async def answer(self, bound: BoundRequest) -> dict[str, Any]:
        """Ask a person and translate the outcome into an ACP response payload.

        Every failure path — denial, timeout, interrupt, channel error, a Stop that
        landed meanwhile, a changed operation, a duplicate request — ends in
        ``cancelled`` or an explicit one-shot reject. Only a live ``True`` from the
        broker, revalidated against this exact operation, becomes ``allow_once``.
        """
        if self._shutdown.is_set():
            return self._finish(bound, CANCELLED_RESPONSE, "cancelled")
        approved, reason = False, "handler_failed"
        try:
            outcome = await self._approver(bound.details)
            if isinstance(outcome, tuple) and len(outcome) == 2:
                approved, reason = bool(outcome[0]), str(outcome[1])
            else:  # an approver that only returns a bool cannot claim a reason
                approved, reason = bool(outcome), ("resolved" if outcome else "rejected")
        except asyncio.CancelledError:
            self._finish(bound, CANCELLED_RESPONSE, "cancelled")
            raise
        except Exception:  # noqa: BLE001 - a failed channel is not consent
            approved, reason = False, "handler_failed"

        if approved and reason == "resolved":
            if self._revalidate(bound):
                return self._finish(bound, allowed_response(bound.allow_option), "allowed")
            return self._finish(bound, CANCELLED_RESPONSE, "cancelled")
        if not approved and reason == "resolved":
            return self._finish(bound, rejected_response(bound.reject_option), "rejected")
        return self._finish(bound, CANCELLED_RESPONSE, "cancelled")

    def _revalidate(self, bound: BoundRequest) -> bool:
        """The operation must still be the one the person approved, right now."""
        return self._operation_current(bound, require_live=True)

    def _operation_current(self, bound: BoundRequest, *, require_live: bool) -> bool:
        if self._shutdown.is_set() or self._is_closed():
            return False
        if int(self._epoch() or 0) != bound.stop_epoch:
            # A Stop landed after the request was bound: cancellation wins, even over an
            # allow that was already resolved.
            return False
        with self._lock:
            if self._turn != bound.record.turn:
                return False
            if require_live and self._live.get(bound.request_id) is not bound:
                return False
            current = self._calls.get(bound.record.tool_call_id)
            if current is None or current.binding() != bound.record.binding():
                return False
        # The existing approval binding, rebuilt from the same inputs: a changed
        # argument, resolved path, target or policy would produce another fingerprint.
        return bound.plan.matches(bound.request, bound.decision, bound.policy,
                                  execution_target=bound.target)

    def guard_response(self, bound: BoundRequest,
                       response: Mapping[str, Any]) -> Mapping[str, Any]:
        """The last check, run by the writer immediately before the bytes are written.

        Between the human's answer and the wire there can be an arbitrary wait (a blocked
        pipe, a paused thread). A Stop that lands in that window must still win, so an
        allow is downgraded to ``cancelled`` unless the operation is *still* current at the
        instant of the write. A reject or cancel is never upgraded.
        """
        outcome = ((response.get("result") or {}).get("outcome") or {})
        if outcome.get("optionId") != bound.allow_option:
            return response
        if self._operation_current(bound, require_live=False):
            return response
        with self._lock:
            self.stats.retracted += 1
        return CANCELLED_RESPONSE

    def _finish(self, bound: BoundRequest, response: dict[str, Any], state: str) -> dict[str, Any]:
        with self._lock:
            bound.state = state
            self._live.pop(bound.request_id, None)
            self._decided.add(bound.record.tool_call_id)
            counter = {"allowed": "allowed", "rejected": "rejected",
                       "cancelled": "cancelled"}.get(state)
            if counter is not None:
                setattr(self.stats, counter, getattr(self.stats, counter) + 1)
        return response

    # ------------------------------------------------------------------ failures

    def note_unbindable(self, reason: str) -> None:
        with self._lock:
            self.stats.unbindable += 1
            self._failures.append(("unbindable", reason))

    def note_unsupported(self, method: str) -> None:
        """A request that is not a permission ask. A question is named as a question."""
        with self._lock:
            if method in QUESTION_METHODS:
                self.stats.questions += 1
                self._failures.append(("question", method))
            else:
                self.stats.unsupported += 1
                self._failures.append(("unsupported", method))

    def take_failures(self) -> list[tuple[str, str]]:
        """Drain what went wrong this turn, for the runtime to report or refuse on."""
        with self._lock:
            failures, self._failures = self._failures, []
            return failures

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "turn": self._turn,
                "session": self._session_id,
                "calls": {key: {"name": value.name, "status": value.status}
                          for key, value in self._calls.items()},
                "pending": [str(key) for key in self._live],
                "decided": sorted(self._decided),
                "stats": self.stats.public(),
            }
