"""Focused tests for the pre-execution rejection -> correction loop.

Scope under test (``OdessyAgent.run_turn``): schema/argument validation failures
are retryable and are fed back to the model as an observation, so it can propose
a corrected ``ToolRequest``. Authority outcomes are NOT retryable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from odessy.agent_loop import ExecutionTarget, OdessyAgent
from odessy.mcp_server import build_odessy_mcp_server
from odessy.policy import OdessyPolicy
from odessy.types import FinalResponse, ModelAction, ToolRequest
from odessy.tool_timeout import TOOL_TIMEOUT


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class ScriptedModel:
    """Returns a scripted sequence of actions, then repeats the last one.

    ``respond_to_observation`` advances the script, which is how the loop asks
    for a correction after a rejection.
    """

    def __init__(self, actions: list[ModelAction]) -> None:
        self._actions = list(actions)
        self.propose_calls = 0
        self.observation_calls = 0
        self.observations: list[Any] = []

    def propose(self, user_text: str) -> ModelAction:
        self.propose_calls += 1
        return self._next()

    def respond_to_observation(self, user_text: str, observation: Any) -> ModelAction:
        self.observation_calls += 1
        self.observations.append(observation)
        return self._next()

    def _next(self) -> ModelAction:
        if not self._actions:
            raise AssertionError("ScriptedModel ran out of scripted actions")
        action = self._actions[0]
        if len(self._actions) > 1:
            self._actions.pop(0)
        return action


def _bad_call() -> ToolRequest:
    """Schema-invalid but policy-clean: an unexpected argument.

    ``terminal.run`` declares command/cwd/timeout, so ``bogus`` is caught by the
    MCP schema check rather than by the policy layer.
    """

    return ToolRequest(
        name="terminal.run",
        arguments={"command": "pwd", "cwd": ".", "timeout": 10, "bogus": 1},
    )


def _good_call() -> ToolRequest:
    return ToolRequest(
        name="terminal.run",
        # These tests measure rejection handling, not PowerShell startup latency.
        arguments={"command": "pwd", "cwd": ".", "timeout": 30},
    )


def build_agent(
    workspace_root: Path,
    model: ScriptedModel,
    *,
    max_rejections: int = 2,
    deadline_seconds: float | None = None,
    confirm: bool = False,
) -> OdessyAgent:
    return OdessyAgent(
        model=model,
        policy=OdessyPolicy(workspace_root=workspace_root),
        mcp_server=ExecutionTarget(build_odessy_mcp_server(workspace_root=workspace_root)),
        confirm=confirm,
        max_rejections=max_rejections,
        deadline_seconds=deadline_seconds,
    )


@pytest.mark.anyio
async def test_rejected_call_is_corrected_and_then_executes(tmp_path: Path) -> None:
    model = ScriptedModel([_bad_call(), _good_call(), FinalResponse(text="done")])
    agent = build_agent(tmp_path, model)

    response = await agent.run_turn("run pwd")

    events = [entry.event for entry in response.log]
    assert "schema_rejected" in events
    assert "tool_rejection_observation" in events
    assert "schema_validated" in events

    # The corrected call actually ran.
    assert response.tool_result is not None
    assert response.tool_result["status"] == "ok", response.tool_result
    assert response.tool_result["exit_code"] == 0

    # Two observation calls: one to deliver the rejection, one to summarise the
    # successful result. The rejection must be delivered as an observation
    # carrying its reason.
    assert model.observation_calls == 2
    delivered = model.observations[0]
    assert delivered.result["status"] == "rejected"
    assert "bogus" in delivered.result["reason"]


@pytest.mark.anyio
async def test_rejection_observation_is_not_treated_as_a_tool_result(
    tmp_path: Path,
) -> None:
    """A rejection must never look like a successful execution."""

    model = ScriptedModel([_bad_call(), _good_call(), FinalResponse(text="done")])
    agent = build_agent(tmp_path, model)

    response = await agent.run_turn("run pwd")

    assert "mcp_tool_called" not in [
        entry.event for entry in response.log
    ] or [entry.event for entry in response.log].count("mcp_tool_called") == 1


@pytest.mark.anyio
async def test_model_may_finalise_instead_of_correcting(tmp_path: Path) -> None:
    model = ScriptedModel([_bad_call(), FinalResponse(text="I cannot do that.")])
    agent = build_agent(tmp_path, model)

    response = await agent.run_turn("run pwd")

    assert response.final_text == "I cannot do that."
    assert response.tool_result is None
    assert "tool_rejection_observation" in [entry.event for entry in response.log]


@pytest.mark.anyio
async def test_rejection_budget_is_bounded_and_never_executes(tmp_path: Path) -> None:
    """An uncorrectable model must terminate honestly rather than loop forever."""

    model = ScriptedModel([_bad_call()])
    agent = build_agent(tmp_path, model, max_rejections=2)

    response = await agent.run_turn("run pwd")

    assert "schema_rejection_exhausted" in [entry.event for entry in response.log]
    assert response.tool_result is None
    assert "could not form a valid call" in response.final_text

    # With max_rejections=2 the loop permits two correction attempts; the third
    # rejection exhausts the budget.
    assert model.observation_calls == 2
    assert model.propose_calls == 1


@pytest.mark.anyio
async def test_zero_rejection_budget_never_corrects(tmp_path: Path) -> None:
    model = ScriptedModel([_bad_call()])
    agent = build_agent(tmp_path, model, max_rejections=0)

    response = await agent.run_turn("run pwd")

    assert model.observation_calls == 0
    assert "schema_rejection_exhausted" in [entry.event for entry in response.log]


@pytest.mark.anyio
async def test_expired_deadline_stops_correction(tmp_path: Path) -> None:
    """Wall clock bounds the loop independently of the attempt count."""

    model = ScriptedModel([_bad_call(), _good_call()])
    agent = build_agent(tmp_path, model, max_rejections=5, deadline_seconds=0.0)

    response = await agent.run_turn("run pwd")

    assert "schema_rejection_exhausted" in [entry.event for entry in response.log]
    assert model.observation_calls == 0


@pytest.mark.anyio
async def test_policy_denial_is_not_retryable(tmp_path: Path) -> None:
    """Authority outcomes must keep raising, never entering the retry loop."""

    # ScriptedModel ignores the user text, so the destructive call is explicit.
    destructive = ToolRequest(
        name="terminal.run",
        arguments={"command": "rm -rf .", "cwd": ".", "timeout": 10},
    )
    model = ScriptedModel([destructive, FinalResponse(text="retried")])
    agent = build_agent(tmp_path, model)

    with pytest.raises(PermissionError, match="denied"):
        await agent.run_turn("run rm -rf .")

    # No correction was attempted: the policy layer is not arguable.
    assert model.observation_calls == 0


@pytest.mark.anyio
async def test_valid_call_is_unaffected(tmp_path: Path) -> None:
    """The rejection machinery must not perturb the success path.

    The event list is asserted exactly so that adding a rejection branch cannot
    quietly change what a valid call does. One entry was added deliberately: the
    authority layer now records the execution mode it resolved, which is the audit
    trail for what the command actually ran under.
    """

    model = ScriptedModel([_good_call(), FinalResponse(text="ok")])
    agent = build_agent(tmp_path, model)

    response = await agent.run_turn("run pwd")

    assert model.observation_calls == 1  # the normal final-response call
    assert response.tool_result is not None
    assert response.tool_result["status"] == "ok", repr(response.tool_result)
    assert [entry.event for entry in response.log] == [
        "user_input",
        "model_tool_request",
        "schema_validated",
        "policy_allowed",
        "execution_mode_resolved",
        "mcp_tool_called",
        "tool_observation",
        "model_final_response",
        "agent_turn_metrics",
    ]


@pytest.mark.anyio
async def test_second_tool_after_successful_execution_continues(tmp_path: Path) -> None:
    """A ToolRequest returned after execution must continue, not crash.

    Regression: ``respond_to_observation`` can now return ``ModelAction``, and the
    post-execution path used to read ``.text`` unconditionally. A model that
    chained tools raised ``AttributeError``.
    """

    model = ScriptedModel(
        [
            ToolRequest(name="time.now", arguments={}),
            ToolRequest(name="calculator.eval", arguments={"expression": "2 + 2"}),
            FinalResponse(text="Both calls finished."),
        ]
    )
    agent = build_agent(tmp_path, model)

    response = await agent.run_turn("what time is it, and what is 2 + 2?")

    events = [entry.event for entry in response.log]
    assert events.count("mcp_tool_called") == 2
    assert response.final_text == "Both calls finished."

    # The reported result is the second execution's, not the first's.
    assert response.tool_request is not None
    assert response.tool_request.name == "calculator.eval"
    assert response.tool_result is not None
    assert response.tool_result["result"] == 4


@pytest.mark.anyio
async def test_multi_tool_chain_is_bounded_by_model_calls(tmp_path: Path) -> None:
    """An endless tool chain must stop instead of generating without limit."""

    model = ScriptedModel([ToolRequest(name="time.now", arguments={})])
    agent = build_agent(tmp_path, model)
    agent._max_model_calls = 3

    response = await agent.run_turn("keep calling tools forever")

    assert "continuation_budget_exhausted" in [entry.event for entry in response.log]
    assert "reached its limit on model calls" in response.final_text
    assert response.tool_result is not None


@pytest.mark.anyio
async def test_repeat_guard_fires_on_repeated_identical_calls(tmp_path: Path) -> None:
    """The guard must observe real ToolRequests on the live path."""

    model = ScriptedModel([ToolRequest(name="time.now", arguments={})])
    agent = build_agent(tmp_path, model)
    agent._max_model_calls = 6

    response = await agent.run_turn("call time.now repeatedly")

    assert "repeat_tool_reminder" in [entry.event for entry in response.log]


@pytest.mark.anyio
async def test_bool_is_rejected_where_an_integer_is_required(tmp_path: Path) -> None:
    """The unified validator must not accept True as an integer."""

    model = ScriptedModel(
        [
            ToolRequest(
                name="terminal.run",
                arguments={"command": "pwd", "cwd": ".", "timeout": True},
            )
        ]
    )
    agent = build_agent(tmp_path, model, max_rejections=0)

    response = await agent.run_turn("run pwd")

    assert response.tool_result is None
    reasons = [
        entry.detail.get("reason")
        for entry in response.log
        if entry.event == "schema_rejected"
    ]
    assert reasons and "must be an integer" in reasons[0]


def test_timeout_result_is_classified_as_tool_timeout(tmp_path: Path) -> None:
    """A result the execution layer reported as timed out becomes TOOL_TIMEOUT.

    Covers the integration point in the loop: ``_classify_timeout`` runs on every
    structured result and owns the ``TOOL_TIMEOUT`` code, so a later retry or
    escalation layer can route on it without parsing prose.
    """

    agent = build_agent(tmp_path, ScriptedModel([_good_call()]))

    info = agent._classify_timeout(
        "terminal.run", {"status": "timeout", "duration": 30.0, "exit_code": None}
    )

    assert info is not None
    assert info["is_error"] is True
    assert info["error"]["info"]["code"] == TOOL_TIMEOUT
    assert info["error"]["info"]["tool"] == "terminal.run"
    assert info["error"]["info"]["timeout_seconds"] == 30
    assert "timed out after 30s" in info["content"][0]["text"]


def test_non_timeout_results_are_not_classified(tmp_path: Path) -> None:
    """Ordinary successes and failures must not be relabelled as timeouts."""

    agent = build_agent(tmp_path, ScriptedModel([_good_call()]))

    assert agent._classify_timeout("terminal.run", {"status": "ok", "duration": 0.2}) is None
    assert (
        agent._classify_timeout("terminal.run", {"status": "failed", "duration": 0.2})
        is None
    )
    # A result without a recognisable status is left alone too.
    assert agent._classify_timeout("terminal.run", {}) is None


def _slow_command() -> str:
    """A command that outlives a 1 s budget, on either platform."""

    return "Start-Sleep -Seconds 5" if os.name == "nt" else "sleep 5"


@pytest.mark.anyio
async def test_tool_timeout_is_classified_through_live_execution(tmp_path: Path) -> None:
    """A real execution that exceeds its budget is tagged on the live path.

    This exercises the integration rather than the classifier in isolation: the
    model calls `terminal.run` with a 1 s budget, the command sleeps past it, the
    bridge reports `status: "timeout"`, and the loop tags it with TOOL_TIMEOUT.
    """

    model = ScriptedModel(
        [
            ToolRequest(
                name="terminal.run",
                arguments={"command": _slow_command(), "cwd": ".", "timeout": 1},
            ),
            FinalResponse(text="That command timed out."),
        ]
    )
    # A sleep command falls outside the low-risk allow rules, so the turn passes
    # through confirmation. That gate is not what this test is about.
    agent = build_agent(tmp_path, model, confirm=True)

    response = await agent.run_turn("run a slow command")

    # The bridge reported the timeout, and the loop classified it.
    assert response.tool_result is not None
    assert response.tool_result["status"] == "timeout"
    assert response.tool_result["timed_out"] is True

    timeout_entries = [e for e in response.log if e.event == "tool_timeout"]
    assert len(timeout_entries) == 1
    assert timeout_entries[0].detail["name"] == "terminal.run"
    assert timeout_entries[0].detail["error"]["info"]["code"] == TOOL_TIMEOUT
