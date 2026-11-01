"""The escalation vocabulary and choreography.

Reimplemented for Python from ``dsh-sandbox/src/escalation.ts``. The strictly
wider ladder, the argument-pairing validation, and every user-facing text are
preserved verbatim.

The approval channel is a minimal *structural* shape rather than a concrete
approval service, so this module never depends on the tool or agent packages: the
tool layer — which owns the agent identity, the call id, and the tool name —
closes over its own approval mechanism and hands it down.

Two things are deliberately checked at *execution* time rather than declared in a
tool schema:

* **Strict widening.** The schema advertises the closed target vocabulary; the
  effective mode is per-call truth. A schema cannot express "strictly wider than
  whatever this particular call is currently running under".
* **The argument pairing.** ``sandbox_permissions`` and ``justification`` travel
  together. An approval prompt without a reason, or a reason driving nothing, is a
  malformed ask.

A non-widening request never prompts a human. Otherwise the escalation mechanism
would itself be the bypass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

from odessy.execution.contract import (
    ESCALATION_TARGETS,
    WIDER_MODES,
    EscalationOutcome,
    Mode,
    escalation_hint,
    sandbox_denial_marker,
)

__all__ = [
    "ESCALATION_TARGETS",
    "ApprovalRequest",
    "EscalationApproval",
    "EscalationApprover",
    "EscalationArgumentError",
    "EscalationDeniedError",
    "EscalationError",
    "EscalationOutcome",
    "EscalationRequest",
    "approve_escalation",
    "escalation_hint",
    "sandbox_denial_marker",
    "validate_escalation_args",
]


# --------------------------------------------------------------------------
# Failure modes
# --------------------------------------------------------------------------


class EscalationError(RuntimeError):
    """Base class for escalation failures."""


class EscalationArgumentError(EscalationError):
    """The escalation arguments themselves are malformed.

    This is a model or caller error, not a policy outcome. Odessy's agent loop
    treats it as a recoverable rejection: the request can be corrected and retried
    without human involvement.
    """


class EscalationDeniedError(EscalationError):
    """The escalation was refused, by the ladder, by configuration, or by a human.

    Odessy's agent loop treats this as a non-retryable policy outcome, matching
    the existing rule that a policy denial is not retried.
    """


# --------------------------------------------------------------------------
# Verbatim user-facing texts, preserved from upstream
# --------------------------------------------------------------------------

_NON_WIDENING = """sandbox escalation to "{mode}" is not strictly wider than this call's current "{effective}" mode"""
_NO_APPROVER = """sandbox escalation to "{mode}" requires approval, but no approval service is composed"""
_NO_AGENT = """sandbox escalation to "{mode}" requires approval, but the call has no agent to route it through"""
_REJECTED = 'the user rejected escalating this {subject} to "{mode}"'
_CANCELLED = 'approval for escalating to "{mode}" was cancelled'
_UNAVAILABLE = """sandbox escalation to "{mode}" requires approval, but no approval channel is available"""
_APPROVAL_REASON = "escalate sandbox to {mode}: {justification}"


# --------------------------------------------------------------------------
# Argument validation
# --------------------------------------------------------------------------


def validate_escalation_args(
    sandbox_permissions: str | None,
    justification: str | None,
) -> None:
    """Validate the argument pairing a tool schema cannot express.

    The two arguments travel together, and the justification must be a non-empty
    sentence rather than whitespace that satisfies a length check.
    """

    if sandbox_permissions is not None and justification is None:
        raise EscalationArgumentError(
            "invalid escalation: sandbox_permissions requires a justification"
        )
    if justification is not None and sandbox_permissions is None:
        raise EscalationArgumentError(
            "invalid escalation: justification is only valid together with sandbox_permissions"
        )
    if justification is not None and justification.strip() == "":
        raise EscalationArgumentError("invalid justification: expected a non-empty sentence")


# --------------------------------------------------------------------------
# The approval channel
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """One approval ask, self-contained so it can be recorded for the audit trail."""

    agent: object
    tool_name: str
    call_id: str
    reason: str
    #: The authority's own description of the call (the exact command, the target),
    #: which ``reason`` is not: that carries the model's justification.
    prompt: str = ""


@runtime_checkable
class EscalationApprover(Protocol):
    """The minimal channel :func:`approve_escalation` needs.

    Structurally this is Odessy's confirmation seam; the tool layer adapts its own
    mechanism to it rather than this module importing the tool or agent packages.
    """

    async def ask(self, request: ApprovalRequest) -> EscalationOutcome:
        """Ask the human to approve one action, resolving to a closed outcome."""
        ...


@dataclass(frozen=True, slots=True)
class EscalationApproval:
    """The approval ingredients an escalating tool holds."""

    #: The approval requester, or ``None`` when none is composed. Fails closed.
    approver: EscalationApprover | None
    #: The calling agent, or ``None`` for an agent-less execution. Fails closed.
    agent: object | None
    #: The tool-call id the approval prompt attaches to.
    call_id: str
    #: The tool name recorded on the approval request.
    tool_name: str
    #: What a person must see before approving; see ``ApprovalRequest.prompt``.
    prompt: str = ""


# --------------------------------------------------------------------------
# The request
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EscalationRequest:
    """One escalation request, as :func:`approve_escalation` judges it."""

    #: The requested target mode. Advertised to the model as `ESCALATION_TARGETS`.
    requested_mode: str
    #: The model's one-sentence reason, shown verbatim inside the audit reason.
    justification: str
    #: The call's effective mode, which the request must strictly widen.
    effective_mode: Mode
    #: The family's noun for the escalated action (`command`, `operation`).
    subject: str


# --------------------------------------------------------------------------
# Choreography
# --------------------------------------------------------------------------


async def approve_escalation(
    request: EscalationRequest,
    approval: EscalationApproval,
) -> Mode:
    """Resolve an escalation request *before* anything executes.

    The ordered, fail-closed sequence: strict widening, then the approval channel,
    then the outcome. Returns the granted mode, which applies to exactly the one
    call that asked; an approved escalation is a *new* call with a wider policy,
    never a mutated one.

    Every other path raises, and nothing has run.
    """

    mode = request.requested_mode
    effective = request.effective_mode

    # Strict widening is an execution check against the call's effective mode,
    # deliberately not a schema constraint.
    if mode not in WIDER_MODES.get(effective, ()):
        raise EscalationDeniedError(_NON_WIDENING.format(mode=mode, effective=effective))

    if approval.approver is None:
        raise EscalationDeniedError(_NO_APPROVER.format(mode=mode))

    if approval.agent is None:
        raise EscalationDeniedError(_NO_AGENT.format(mode=mode))

    outcome = await approval.approver.ask(
        ApprovalRequest(
            agent=approval.agent,
            tool_name=approval.tool_name,
            call_id=approval.call_id,
            reason=_APPROVAL_REASON.format(mode=mode, justification=request.justification),
            prompt=approval.prompt,
        )
    )

    if outcome == "allowed-once":
        return cast(Mode, mode)
    if outcome == "rejected":
        raise EscalationDeniedError(_REJECTED.format(subject=request.subject, mode=mode))
    if outcome == "cancelled":
        raise EscalationDeniedError(_CANCELLED.format(mode=mode))
    if outcome == "unavailable":
        raise EscalationDeniedError(_UNAVAILABLE.format(mode=mode))

    # Exhaustiveness: a new outcome must be handled here rather than silently
    # falling through to a grant. Mirrors upstream's `assertNever`.
    raise AssertionError(f"unhandled escalation outcome: {outcome!r}")
