"""Isolation requirement and escalation in the authority layer.

Phase 8 adds two things to `OdessyPolicy` that the authority decision alone could not
express:

1. **Which mode an action runs under.** Derived from the authority decision already
   reached, never from a second pass over the command rules — two evaluations could
   disagree, and the authority answer would then describe a different action from the
   isolation answer.
2. **Escalation**, which is the one path that can widen that mode, and the single
   invariant that stops it handing out more than the requirement allows.

The exception the whole phase exists for is asserted directly:
`danger-full-access` can never be granted when isolation is `required`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odessy.execution.contract import Requirement
from odessy.execution.escalation import (
    ApprovalRequest,
    EscalationApproval,
    EscalationDeniedError,
    EscalationOutcome,
)
from odessy.authority.policy import OdessyPolicy
from odessy.runtime.types import PolicyDecision, ToolRequest


class _Approver:
    """A recorded approval channel returning one fixed outcome."""

    def __init__(self, outcome: EscalationOutcome = "allowed-once") -> None:
        self.outcome = outcome
        self.requests: list[ApprovalRequest] = []

    async def ask(self, request: ApprovalRequest) -> EscalationOutcome:
        self.requests.append(request)
        return self.outcome


def _policy(tmp_path: Path, **kwargs: object) -> OdessyPolicy:
    return OdessyPolicy(workspace_root=tmp_path, **kwargs)  # type: ignore[arg-type]


def _approval(approver: object | None = None, agent: object | None = None) -> EscalationApproval:
    # `agent` defaults to a real object because a None agent fails closed, which is a
    # separate case the tests below assert on purpose.
    return EscalationApproval(
        approver=approver,  # type: ignore[arg-type]
        agent=object() if agent is None else agent,
        call_id="call-1",
        tool_name="terminal.run",
    )


def _request(command: str, **extra: object) -> ToolRequest:
    return ToolRequest(name="terminal.run", arguments={"command": command, **extra})


# --------------------------------------------------------------------------
# Which mode an action runs under
# --------------------------------------------------------------------------


def test_a_read_only_command_resolves_to_the_read_only_mode(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    request = _request("ls")

    decision = policy.evaluate(request)
    requirement = policy.requirement_for(request, decision)

    assert decision.action == "allow"
    assert isinstance(requirement, Requirement)
    assert requirement.policy.mode == "read-only"
    assert requirement.isolation == "preferred"


def test_a_state_modifying_command_resolves_to_workspace_write(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    request = _request("mkdir build")

    decision = policy.evaluate(request)
    requirement = policy.requirement_for(request, decision)

    assert decision.action == "confirm"
    assert isinstance(requirement, Requirement)
    assert requirement.policy.mode == "workspace-write"


def test_a_command_outside_the_allow_rules_still_defaults_to_workspace_write(
    tmp_path: Path,
) -> None:
    # It gets `confirm` because it is unrecognised, and an unrecognised command is
    # treated as one that may write rather than one that may not.
    policy = _policy(tmp_path)
    request = _request("frobnicate --now")

    decision = policy.evaluate(request)
    requirement = policy.requirement_for(request, decision)

    assert decision.action == "confirm"
    assert isinstance(requirement, Requirement)
    assert requirement.policy.mode == "workspace-write"


def test_a_denied_command_has_no_mode(tmp_path: Path) -> None:
    # A denied action never executes, so there is no mode to describe.
    policy = _policy(tmp_path)
    request = _request("rm -rf /")

    decision = policy.evaluate(request)

    assert decision.action == "deny"
    assert policy.requirement_for(request, decision) is None


def test_an_action_that_spawns_no_process_has_no_requirement(tmp_path: Path) -> None:
    # This answer is scoped to EXECUTION isolation. It means "the execution axis does
    # not govern this action", not "this action needs no boundary": a managed-device
    # write is not a process and is still a privileged capability, so it must bring its
    # own requirement semantics rather than inheriting this `None`.
    policy = _policy(tmp_path)
    request = ToolRequest(name="calculator.eval", arguments={"expression": "1 + 1"})

    decision = policy.evaluate(request)

    assert decision.action == "allow"
    assert policy.requirement_for(request, decision) is None


def test_the_requirement_carries_the_policy_isolation(tmp_path: Path) -> None:
    policy = _policy(tmp_path, isolation="required")
    request = _request("ls")

    requirement = policy.requirement_for(request, policy.evaluate(request))

    assert requirement is not None
    assert requirement.isolation == "required"


# --------------------------------------------------------------------------
# Malformed escalation asks are denied, not raised
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({"sandbox_permissions": "workspace-write"}, "requires a justification"),
        ({"justification": "because I said so"}, "only valid together"),
        ({"sandbox_permissions": "workspace-write", "justification": "   "}, "non-empty sentence"),
        ({"sandbox_permissions": "read-only", "justification": "let me"}, "unknown sandbox_permissions"),
    ],
)
def test_a_malformed_escalation_ask_is_denied(
    tmp_path: Path, extra: dict[str, object], expected: str
) -> None:
    # `evaluate` must stay total: the agent loop treats a raise from it as a bug rather
    # than an outcome. So the pairing rule surfaces as a denial.
    policy = _policy(tmp_path)

    decision = policy.evaluate(_request("mkdir build", **extra))

    assert decision.action == "deny"
    assert expected in decision.reason


def test_a_well_formed_escalation_ask_is_not_denied(tmp_path: Path) -> None:
    policy = _policy(tmp_path)

    decision = policy.evaluate(
        _request("mkdir build", sandbox_permissions="danger-full-access", justification="needs the network")
    )

    assert decision.action == "confirm"


def test_escalation_is_refused_on_an_action_with_no_sandbox(tmp_path: Path) -> None:
    # A silent no-op would be worse than a refusal: the model would be told nothing and
    # could believe it had widened a mode.
    policy = _policy(tmp_path)
    request = ToolRequest(
        name="calculator.eval",
        arguments={
            "expression": "1 + 1",
            "sandbox_permissions": "danger-full-access",
            "justification": "just in case",
        },
    )

    decision = policy.evaluate(request)

    assert decision.action == "deny"
    assert "sandbox modes govern process-spawning actions" in decision.reason


# --------------------------------------------------------------------------
# The refusal this phase exists for
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_required_isolation_refuses_escalation_to_full_access(tmp_path: Path) -> None:
    # `required` means an absolute boundary was demanded and `danger-full-access`
    # provides none. Granting it would let the escalation path hand out exactly what
    # the requirement exists to refuse. The registry refuses the same combination; the
    # two layers must not disagree.
    policy = _policy(tmp_path, isolation="required")
    approver = _Approver("allowed-once")
    request = _request(
        "mkdir build", sandbox_permissions="danger-full-access", justification="needs the network"
    )

    with pytest.raises(EscalationDeniedError, match="provides no confinement"):
        await policy.resolve_execution_mode(
            request, policy.evaluate(request), approval=_approval(approver)
        )

    # Refused before the human is ever asked: there is nothing to approve.
    assert approver.requests == []


@pytest.mark.anyio
async def test_required_isolation_still_allows_a_confined_escalation(tmp_path: Path) -> None:
    # The refusal is specific to the mode that provides nothing. Widening from
    # read-only to workspace-write is still a boundary.
    policy = _policy(tmp_path, isolation="required")
    approver = _Approver("allowed-once")
    request = _request("ls", sandbox_permissions="workspace-write", justification="needs to build")

    granted = await policy.resolve_execution_mode(
        request, policy.evaluate(request), approval=_approval(approver)
    )

    assert granted == "workspace-write"
    assert len(approver.requests) == 1


@pytest.mark.anyio
async def test_preferred_isolation_allows_escalation_to_full_access(tmp_path: Path) -> None:
    # The refusal is a property of the requirement, not of the mode.
    policy = _policy(tmp_path, isolation="preferred")
    approver = _Approver("allowed-once")
    request = _request(
        "mkdir build", sandbox_permissions="danger-full-access", justification="needs the network"
    )

    granted = await policy.resolve_execution_mode(
        request, policy.evaluate(request), approval=_approval(approver)
    )

    assert granted == "danger-full-access"


# --------------------------------------------------------------------------
# The choreography
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_no_escalation_ask_never_prompts_a_human(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    approver = _Approver("allowed-once")
    request = _request("mkdir build")

    granted = await policy.resolve_execution_mode(
        request, policy.evaluate(request), approval=_approval(approver)
    )

    assert granted == "workspace-write"
    # Otherwise the escalation mechanism would itself be the bypass.
    assert approver.requests == []


@pytest.mark.anyio
async def test_an_approved_escalation_returns_the_requested_mode(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    approver = _Approver("allowed-once")
    request = _request("mkdir build", sandbox_permissions="danger-full-access", justification="needs the network")

    granted = await policy.resolve_execution_mode(
        request, policy.evaluate(request), approval=_approval(approver)
    )

    assert granted == "danger-full-access"
    # The justification reaches the approval prompt verbatim.
    assert "needs the network" in approver.requests[0].reason
    assert approver.requests[0].call_id == "call-1"


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("rejected", "the user rejected"),
        ("cancelled", "was cancelled"),
        ("unavailable", "no approval channel is available"),
    ],
)
@pytest.mark.anyio
async def test_a_non_granting_outcome_denies(
    tmp_path: Path, outcome: EscalationOutcome, expected: str
) -> None:
    policy = _policy(tmp_path)
    request = _request("mkdir build", sandbox_permissions="danger-full-access", justification="needs the network")

    with pytest.raises(EscalationDeniedError, match=expected):
        await policy.resolve_execution_mode(
            request, policy.evaluate(request), approval=_approval(_Approver(outcome))
        )


@pytest.mark.anyio
async def test_a_target_that_does_not_strictly_widen_is_denied(tmp_path: Path) -> None:
    # `mkdir` already resolves to workspace-write, so asking for it changes nothing.
    # Strict widening is checked against the call's *effective* mode, which a schema
    # cannot express.
    policy = _policy(tmp_path)
    approver = _Approver("allowed-once")
    request = _request("mkdir build", sandbox_permissions="workspace-write", justification="same mode")

    with pytest.raises(EscalationDeniedError, match="not strictly wider"):
        await policy.resolve_execution_mode(
            request, policy.evaluate(request), approval=_approval(approver)
        )


@pytest.mark.anyio
async def test_a_missing_approver_fails_closed(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    request = _request("mkdir build", sandbox_permissions="danger-full-access", justification="needs the network")

    with pytest.raises(EscalationDeniedError, match="no approval service is composed"):
        await policy.resolve_execution_mode(
            request,
            policy.evaluate(request),
            approval=EscalationApproval(
                approver=None, agent=object(), call_id="call-1", tool_name="terminal.run"
            ),
        )


@pytest.mark.anyio
async def test_an_agentless_escalation_fails_closed(tmp_path: Path) -> None:
    # There is nowhere to route the prompt, so there is no approval to be had.
    policy = _policy(tmp_path)
    request = _request("mkdir build", sandbox_permissions="danger-full-access", justification="needs the network")

    with pytest.raises(EscalationDeniedError, match="no agent to route it through"):
        await policy.resolve_execution_mode(
            request,
            policy.evaluate(request),
            approval=EscalationApproval(
                approver=_Approver("allowed-once"),
                agent=None,
                call_id="call-1",
                tool_name="terminal.run",
            ),
        )


@pytest.mark.anyio
async def test_an_action_with_no_mode_cannot_be_escalated(tmp_path: Path) -> None:
    # A caller error rather than a policy outcome: there is no mode to widen.
    policy = _policy(tmp_path)
    request = ToolRequest(name="calculator.eval", arguments={"expression": "1 + 1"})

    with pytest.raises(ValueError, match="does not spawn a process"):
        await policy.resolve_execution_mode(
            request, policy.evaluate(request), approval=_approval(_Approver())
        )
