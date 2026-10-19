"""The escalation ladder and its fail-closed choreography.

The property that matters most here is negative: **a non-widening request must
never prompt a human.** If it did, the escalation mechanism would itself be the
bypass around the guarantee it exists to widen deliberately.
"""

from __future__ import annotations

from typing import Any

import pytest

from odessy.execution.contract import ESCALATION_TARGETS
from odessy.execution.escalation import (
    ApprovalRequest,
    EscalationApproval,
    EscalationArgumentError,
    EscalationDeniedError,
    EscalationRequest,
    approve_escalation,
    escalation_hint,
    sandbox_denial_marker,
    validate_escalation_args,
)


class _RecordingApprover:
    """An approval channel that records every ask it receives."""

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.requests: list[ApprovalRequest] = []

    async def ask(self, request: ApprovalRequest) -> Any:
        self.requests.append(request)
        return self.outcome


def _request(
    *,
    mode: str = "workspace-write",
    effective: str = "read-only",
    subject: str = "command",
) -> EscalationRequest:
    return EscalationRequest(
        requested_mode=mode,
        justification="needs to write build output",
        effective_mode=effective,  # type: ignore[arg-type]
        subject=subject,
    )


def _approval(
    approver: Any,
    *,
    agent: Any = "agent-1",
    call_id: str = "call-1",
    tool_name: str = "terminal.run",
) -> EscalationApproval:
    return EscalationApproval(
        approver=approver, agent=agent, call_id=call_id, tool_name=tool_name
    )


# --------------------------------------------------------------------------
# Argument pairing
# --------------------------------------------------------------------------


def test_permissions_without_a_justification_is_rejected() -> None:
    with pytest.raises(EscalationArgumentError, match="sandbox_permissions requires a justification"):
        validate_escalation_args("workspace-write", None)


def test_justification_without_permissions_is_rejected() -> None:
    with pytest.raises(EscalationArgumentError, match="only valid together with"):
        validate_escalation_args(None, "because I said so")


def test_a_whitespace_justification_is_not_a_sentence() -> None:
    with pytest.raises(EscalationArgumentError, match="non-empty sentence"):
        validate_escalation_args("workspace-write", "   ")


def test_a_paired_ask_is_valid() -> None:
    validate_escalation_args("workspace-write", "needs to write build output")


def test_neither_argument_is_valid() -> None:
    validate_escalation_args(None, None)


# --------------------------------------------------------------------------
# Non-widening requests never reach a human
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_equal_mode_is_not_widening_and_never_prompts() -> None:
    approver = _RecordingApprover("allowed-once")

    with pytest.raises(EscalationDeniedError, match="is not strictly wider"):
        await approve_escalation(
            _request(mode="workspace-write", effective="workspace-write"), _approval(approver)
        )

    assert approver.requests == [], "a non-widening request must never prompt a human"


@pytest.mark.anyio
async def test_narrowing_request_never_prompts() -> None:
    approver = _RecordingApprover("allowed-once")

    with pytest.raises(EscalationDeniedError, match="is not strictly wider"):
        await approve_escalation(
            _request(mode="read-only", effective="workspace-write"), _approval(approver)
        )

    assert approver.requests == []


@pytest.mark.anyio
async def test_full_access_effective_mode_has_nothing_wider() -> None:
    approver = _RecordingApprover("allowed-once")

    for target in ESCALATION_TARGETS:
        with pytest.raises(EscalationDeniedError, match="is not strictly wider"):
            await approve_escalation(
                _request(mode=target, effective="danger-full-access"), _approval(approver)
            )

    assert approver.requests == []


@pytest.mark.anyio
async def test_read_only_cannot_escalate_to_itself() -> None:
    approver = _RecordingApprover("allowed-once")

    with pytest.raises(EscalationDeniedError):
        await approve_escalation(
            _request(mode="read-only", effective="read-only"), _approval(approver)
        )

    assert approver.requests == []


# --------------------------------------------------------------------------
# Widening requests that are allowed
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_only_may_escalate_to_workspace_write() -> None:
    approver = _RecordingApprover("allowed-once")

    granted = await approve_escalation(
        _request(mode="workspace-write", effective="read-only"), _approval(approver)
    )

    assert granted == "workspace-write"
    assert len(approver.requests) == 1


@pytest.mark.anyio
async def test_read_only_may_escalate_to_full_access_in_one_step() -> None:
    approver = _RecordingApprover("allowed-once")

    granted = await approve_escalation(
        _request(mode="danger-full-access", effective="read-only"), _approval(approver)
    )

    assert granted == "danger-full-access"


@pytest.mark.anyio
async def test_workspace_write_may_escalate_to_full_access() -> None:
    approver = _RecordingApprover("allowed-once")

    granted = await approve_escalation(
        _request(mode="danger-full-access", effective="workspace-write"), _approval(approver)
    )

    assert granted == "danger-full-access"


@pytest.mark.anyio
async def test_the_approval_carries_the_call_identity_and_the_reason() -> None:
    approver = _RecordingApprover("allowed-once")

    await approve_escalation(
        _request(mode="workspace-write", effective="read-only"), _approval(approver)
    )

    ask = approver.requests[0]
    assert ask.reason == "escalate sandbox to workspace-write: needs to write build output"
    assert ask.agent == "agent-1"
    assert ask.call_id == "call-1"
    assert ask.tool_name == "terminal.run"


# --------------------------------------------------------------------------
# Fail-closed paths
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_approval_service_fails_closed() -> None:
    with pytest.raises(EscalationDeniedError, match="no approval service is composed"):
        await approve_escalation(_request(), _approval(None))


@pytest.mark.anyio
async def test_agentless_execution_fails_closed_without_prompting() -> None:
    approver = _RecordingApprover("allowed-once")

    with pytest.raises(EscalationDeniedError, match="no agent to route it through"):
        await approve_escalation(_request(), _approval(approver, agent=None))

    assert approver.requests == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("rejected", 'the user rejected escalating this command to "workspace-write"'),
        ("cancelled", 'approval for escalating to "workspace-write" was cancelled'),
        (
            "unavailable",
            'sandbox escalation to "workspace-write" requires approval, but no approval '
            "channel is available",
        ),
    ],
)
async def test_every_refusing_outcome_has_its_own_error(outcome: str, expected: str) -> None:
    approver = _RecordingApprover(outcome)

    with pytest.raises(EscalationDeniedError) as info:
        await approve_escalation(_request(), _approval(approver))

    assert str(info.value) == expected


# --------------------------------------------------------------------------
# Markers come from one vocabulary
# --------------------------------------------------------------------------


def test_denial_marker_is_shared_with_the_contract() -> None:
    assert sandbox_denial_marker("read-only") == (
        "[sandbox: file access denied under read-only mode]"
    )


def test_escalation_hint_names_the_family_subject() -> None:
    assert "this exact operation once" in escalation_hint("operation")
    assert "this exact command once" in escalation_hint("command")
