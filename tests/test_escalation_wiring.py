"""The authority layer's isolation decision, wired all the way to the executor.

Phase 8b connects the two halves that Phase 8 left apart: the mode the policy layer
resolves now reaches the code that actually confines, and a human can be asked to
widen it.

The assertions observe the *provider*, not the log. A log line proves the loop
decided something; only the provider proves the decision was the one that confined.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from odessy.agent_loop import ExecutionTarget, OdessyAgent
from odessy.execution.contract import (
    ConfineResult,
    Evaluation,
    ExecutionPolicy,
    ProviderUnavailableError,
    Requirement,
)
from odessy.execution.escalation import EscalationDeniedError
from odessy.mcp_server import build_odessy_mcp_server
from odessy.policy import OdessyPolicy
from odessy.tool_contracts import validate_tool_arguments
from odessy.types import FinalResponse, ToolObservation, ToolRequest


class _RecordingProvider:
    """A backend that records the policy it was asked to confine under."""

    provider_id = "test-recording"

    def __init__(self, script: str = "print('ran')") -> None:
        self._argv = (sys.executable, "-c", script)
        self.modes: list[str] = []

    def confine(self, argv: object, policy: ExecutionPolicy) -> ConfineResult:
        self.modes.append(policy.mode)
        return ConfineResult(
            argv=self._argv,
            enforcement="full",
            limitations=frozenset(),
            denial_signatures=(),
            runner_failure_rules=(),
            provider_id=self.provider_id,
        )


class _ScriptedModel:
    def __init__(self, actions: list[object]) -> None:
        self._actions = list(actions)

    def propose(self, user_text: str) -> Any:
        return self._actions.pop(0)

    def respond_to_observation(self, user_text: str, observation: ToolObservation) -> Any:
        return self._actions.pop(0)


class _Approver:
    def __init__(self, outcome: str = "allowed-once") -> None:
        self.outcome = outcome
        self.asks = 0

    async def ask(self, request: object) -> Any:
        self.asks += 1
        return self.outcome


def _agent(
    tmp_path: Path,
    *actions: object,
    provider: _RecordingProvider | None = None,
    approver: _Approver | None = None,
    confirm: bool = False,
    isolation: str = "preferred",
) -> tuple[OdessyAgent, _RecordingProvider]:
    provider = provider or _RecordingProvider()
    agent = OdessyAgent(
        model=_ScriptedModel(list(actions)),
        policy=OdessyPolicy(workspace_root=tmp_path, isolation=isolation),  # type: ignore[arg-type]
        mcp_server=ExecutionTarget(
            build_odessy_mcp_server(
                workspace_root=tmp_path,
                provider=provider,
                policy=ExecutionPolicy(mode="read-only", workspace_root=str(tmp_path)),
            )
        ),
        confirm=confirm,
        approver=approver,
    )
    return agent, provider


# --------------------------------------------------------------------------
# The resolved mode reaches the executor
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_read_only_command_confines_under_read_only(tmp_path: Path) -> None:
    agent, provider = _agent(
        tmp_path,
        ToolRequest(name="terminal.run", arguments={"command": "pwd"}),
        FinalResponse(text="done"),
    )

    await agent.run_turn("run pwd")

    assert provider.modes == ["read-only"]


@pytest.mark.anyio
async def test_a_state_modifying_command_confines_under_workspace_write(tmp_path: Path) -> None:
    agent, provider = _agent(
        tmp_path,
        ToolRequest(name="terminal.run", arguments={"command": "mkdir build"}),
        FinalResponse(text="done"),
        confirm=True,
    )

    await agent.run_turn("create a directory")

    assert provider.modes == ["workspace-write"]


@pytest.mark.anyio
async def test_an_action_that_spawns_no_process_never_reaches_a_provider(tmp_path: Path) -> None:
    # `requirement_for` returns None for a non-process action, so there is no mode to
    # resolve and nothing to confine. Scoped to the execution axis: it does not mean
    # the action is unprivileged.
    agent, provider = _agent(
        tmp_path,
        ToolRequest(name="calculator.eval", arguments={"expression": "1 + 1"}),
        FinalResponse(text="done"),
    )

    await agent.run_turn("what is 1 + 1")

    assert provider.modes == []


# --------------------------------------------------------------------------
# Escalation cannot bypass the pipeline
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_an_approved_escalation_reaches_the_executor(tmp_path: Path) -> None:
    approver = _Approver("allowed-once")
    agent, provider = _agent(
        tmp_path,
        ToolRequest(
            name="terminal.run",
            arguments={
                "command": "pwd",
                "sandbox_permissions": "danger-full-access",
                "justification": "needs to reach the network",
            },
        ),
        FinalResponse(text="done"),
        approver=approver,
    )

    await agent.run_turn("run pwd")

    assert provider.modes == ["danger-full-access"]
    assert approver.asks == 1


@pytest.mark.anyio
async def test_the_escalation_arguments_never_reach_the_executor(tmp_path: Path) -> None:
    # They are the requester's vocabulary, and the executor has no use for them: it
    # receives the GRANTED mode and nothing else. That the call succeeds at all proves
    # they were stripped, because the tool schema does not accept them.
    agent, provider = _agent(
        tmp_path,
        ToolRequest(
            name="terminal.run",
            arguments={
                "command": "pwd",
                "sandbox_permissions": "workspace-write",
                "justification": "needs to write",
            },
        ),
        FinalResponse(text="done"),
        approver=_Approver("allowed-once"),
    )

    response = await agent.run_turn("run pwd")

    assert provider.modes == ["workspace-write"]
    assert response.tool_result is not None
    assert response.tool_result["status"] == "ok"


@pytest.mark.anyio
async def test_a_refused_escalation_stops_before_anything_confines(tmp_path: Path) -> None:
    # Invariant: a refused escalation must not fall back to running under the narrower
    # mode. The command does not run at all.
    agent, provider = _agent(
        tmp_path,
        ToolRequest(
            name="terminal.run",
            arguments={
                "command": "pwd",
                "sandbox_permissions": "danger-full-access",
                "justification": "needs to reach the network",
            },
        ),
        FinalResponse(text="done"),
        approver=_Approver("rejected"),
    )

    with pytest.raises(EscalationDeniedError):
        await agent.run_turn("run pwd")

    assert provider.modes == []


@pytest.mark.anyio
async def test_required_isolation_refuses_full_access_escalation(tmp_path: Path) -> None:
    # The invariant the whole pipeline exists for: `isolation=required` must never
    # silently downgrade to unconfined execution, not even with a human's approval.
    approver = _Approver("allowed-once")
    agent, provider = _agent(
        tmp_path,
        ToolRequest(
            name="terminal.run",
            arguments={
                "command": "pwd",
                "sandbox_permissions": "danger-full-access",
                "justification": "needs to reach the network",
            },
        ),
        FinalResponse(text="done"),
        approver=approver,
        isolation="required",
    )

    with pytest.raises(EscalationDeniedError):
        await agent.run_turn("run pwd")

    assert provider.modes == []
    # Refused before the human was asked: there was nothing for them to approve.
    assert approver.asks == 0


@pytest.mark.anyio
async def test_a_missing_approver_fails_closed(tmp_path: Path) -> None:
    # An escalation that cannot be asked for is an escalation that cannot be granted.
    agent, provider = _agent(
        tmp_path,
        ToolRequest(
            name="terminal.run",
            arguments={
                "command": "pwd",
                "sandbox_permissions": "workspace-write",
                "justification": "needs to write",
            },
        ),
        FinalResponse(text="done"),
        approver=None,
    )

    with pytest.raises(EscalationDeniedError):
        await agent.run_turn("run pwd")

    assert provider.modes == []


# --------------------------------------------------------------------------
# The model cannot choose its own mode
# --------------------------------------------------------------------------


def test_the_model_cannot_set_the_execution_mode() -> None:
    # `mode` is absent from the model-facing schema on purpose. If the model could set
    # it, escalation would be bypassable in a single argument -- which is the whole
    # reason the loop injects it after policy and escalation instead.
    with pytest.raises(ValueError, match="Unexpected argument"):
        validate_tool_arguments("terminal.run", {"command": "pwd", "mode": "danger-full-access"})


def test_the_model_can_request_escalation_but_only_with_a_justification() -> None:
    # The request is legal; whether it is GRANTED is decided elsewhere.
    validate_tool_arguments(
        "terminal.run",
        {
            "command": "pwd",
            "sandbox_permissions": "workspace-write",
            "justification": "needs to write",
        },
    )

    with pytest.raises(ValueError, match="Missing required"):
        validate_tool_arguments("terminal.run", {"sandbox_permissions": "workspace-write"})


# --------------------------------------------------------------------------
# A required isolation is not weakened on this side either
# --------------------------------------------------------------------------


class _PartialProvider:
    """A backend that governs only part of what its modes promise."""

    provider_id = "test-partial"

    def __init__(self) -> None:
        self._argv = (sys.executable, "-c", "print('partial')")
        self.modes: list[str] = []

    def evaluate(self, requirement: Requirement) -> Evaluation:
        if requirement.policy.mode == "danger-full-access":
            return Evaluation(False, "none", frozenset(), "no full access")
        return Evaluation(
            requirement.isolation != "required",
            "partial",
            frozenset({"cannot_restrict_reads"}),
            "partial enforcement",
        )

    def confine(self, argv: object, policy: ExecutionPolicy) -> ConfineResult:
        self.modes.append(policy.mode)
        return ConfineResult(
            argv=self._argv,
            enforcement="partial",
            limitations=frozenset({"cannot_restrict_reads"}),
            denial_signatures=(),
            runner_failure_rules=(),
            provider_id=self.provider_id,
        )


def _partial_agent(tmp_path: Path, provider: _PartialProvider, isolation: str) -> OdessyAgent:
    return OdessyAgent(
        model=_ScriptedModel(
            [ToolRequest(name="terminal.run", arguments={"command": "pwd"}), FinalResponse(text="done")]
        ),
        policy=OdessyPolicy(workspace_root=tmp_path, isolation=isolation),  # type: ignore[arg-type]
        mcp_server=ExecutionTarget(
            build_odessy_mcp_server(
                workspace_root=tmp_path,
                provider=provider,
                policy=ExecutionPolicy(mode="read-only", workspace_root=str(tmp_path)),
            )
        ),
    )


@pytest.mark.anyio
async def test_required_isolation_refuses_a_partial_backend(tmp_path: Path) -> None:
    # This side both decides and executes, so there is no trust boundary to cross. The
    # demand still has to be answered: a `required` isolation satisfied by `partial`
    # enforcement is the same silent downgrade the host refuses.
    #
    # The refusal is RETURNED rather than raised so its reason survives the MCP boundary.
    # Raising flattened it into a generic RuntimeError and discarded the cause -- the
    # command was safely stopped either way, but nothing downstream could tell why.
    provider = _PartialProvider()

    response = await _partial_agent(tmp_path, provider, "required").run_turn("run pwd")

    assert response.tool_result is not None
    assert response.tool_result["status"] == "refused"
    # No process ran, so there is no exit status to report.
    assert response.tool_result["exit_code"] is None
    # The reason is the point: it is what the MCP boundary used to destroy.
    assert "required isolation is not met" in response.tool_result["stderr"]
    # And the command genuinely never reached the backend.
    assert provider.modes == []


@pytest.mark.anyio
async def test_preferred_isolation_accepts_a_partial_backend(tmp_path: Path) -> None:
    # The decisive counterpart: same backend, same mode, only the requirement differs.
    provider = _PartialProvider()

    await _partial_agent(tmp_path, provider, "preferred").run_turn("run pwd")

    assert provider.modes == ["read-only"]


# --------------------------------------------------------------------------
# A server must be a declared execution target
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_an_undeclared_server_is_not_an_execution_target(tmp_path: Path) -> None:
    # Refused in full, not merely sent unconfined: running there without a boundary would
    # be treating reachability as trust. This is the seat pairing will occupy -- it
    # establishes nothing, it just stops "reachable" from silently meaning "trusted".
    provider = _RecordingProvider()
    agent = OdessyAgent(
        model=_ScriptedModel(
            [
                ToolRequest(name="terminal.run", arguments={"command": "pwd"}),
                FinalResponse(text="done"),
            ]
        ),
        policy=OdessyPolicy(workspace_root=tmp_path),
        # Deliberately NOT wrapped in `ExecutionTarget`: an unwrapped server is reachable
        # but not authorized, which is exactly the case this test pins.
        mcp_server=build_odessy_mcp_server(
            workspace_root=tmp_path,
            provider=provider,
            policy=ExecutionPolicy(mode="read-only", workspace_root=str(tmp_path)),
        ),
    )

    with pytest.raises(PermissionError, match="not a declared execution target"):
        await agent.run_turn("run pwd")

    assert provider.modes == []


@pytest.mark.anyio
async def test_a_constructed_server_is_a_declared_execution_target(tmp_path: Path) -> None:
    # The default is the set the composition root constructed, so nothing that exists
    # today changes behaviour -- and a dynamically discovered server cannot inherit trust,
    # because it would have to be constructed by the same explicit act.
    agent, provider = _agent(
        tmp_path,
        ToolRequest(name="terminal.run", arguments={"command": "pwd"}),
        FinalResponse(text="done"),
    )

    await agent.run_turn("run pwd")

    assert provider.modes == ["read-only"]
