"""The Python ``terminal.run`` selects its backend per call, and `required` is never bypassed.

Before this, the Python side was never given a backend, and a bridge without one ran
every command unconfined even when the authority layer sent ``isolation: required``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from odessy.authority.command_bridge import CommandBridge
from odessy.authority.policy import OdessyPolicy
from odessy.execution.contract import (
    ConfineResult,
    Evaluation,
    ExecutionPolicy,
    ProbeReport,
    ProviderContractError,
    ProviderUnavailableError,
    UnsatisfiableRequirementError,
)
from odessy.execution.registry import ProviderRegistry, with_unconfined_fallback
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.runtime.types import FinalResponse, ToolRequest
from odessy.tools.mcp_server import build_odessy_mcp_server


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Backend:
    """A selectable backend that confines read-only and workspace-write fully, and
    replaces the argv so a test can see that it ran."""

    provider_id = "test-backend"

    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.policies: list[ExecutionPolicy] = []

    def capabilities(self) -> frozenset[str]:
        return frozenset({"process-exec", "fs-read", "fs-write"})

    def probe(self) -> ProbeReport:
        return ProbeReport(
            provider_id=self.provider_id,
            available=self.available,
            reason="test backend" if self.available else "not installed",
        )

    def evaluate(self, requirement) -> Evaluation:
        confined = requirement.policy.mode in ("read-only", "workspace-write")
        return Evaluation(
            satisfied=confined,
            enforcement="full" if confined else "none",
            limitations=frozenset(),
            reason="confines file writes" if confined else "does not run unconfined modes",
        )

    def confine(self, argv, policy: ExecutionPolicy) -> ConfineResult:
        self.policies.append(policy)
        return ConfineResult(
            argv=(sys.executable, "-c", f"print('confined {policy.mode}')"),
            enforcement="full",
            limitations=frozenset(),
            denial_signatures=(),
            runner_failure_rules=(),
            provider_id=self.provider_id,
        )


# --- the bug: no backend and `required` -----------------------------------------


def test_required_isolation_without_a_backend_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ProviderUnavailableError, match="required isolation is not met"):
        CommandBridge(tmp_path).run(command="echo hi", mode="read-only", isolation="required")


def test_preferred_isolation_without_a_backend_still_runs_and_says_so(tmp_path: Path) -> None:
    result = CommandBridge(tmp_path).run(command="echo hi", timeout=30, mode="read-only", isolation="preferred")
    assert result["status"] == "ok"
    assert result["sandbox"] == {"provider_id": "unconfined", "enforcement": "none", "limitations": []}


def test_the_tool_server_reports_the_refusal(tmp_path: Path) -> None:
    import anyio
    from mcp import Client

    async def call() -> dict:
        async with Client(build_odessy_mcp_server(workspace_root=tmp_path)) as client:
            result = await client.call_tool(
                "terminal.run", {"command": "echo hi", "mode": "read-only", "isolation": "required"}
            )
        return result.structured_content

    result = anyio.run(call)
    assert result["status"] == "refused" and result["exit_code"] is None
    assert "required isolation is not met" in result["stderr"]


# --- selection from a registry --------------------------------------------------


def test_a_registry_backend_is_selected_and_receives_the_mode(tmp_path: Path) -> None:
    backend = _Backend()
    bridge = CommandBridge(tmp_path, registry=with_unconfined_fallback([backend]))
    result = bridge.run(command="echo hi", timeout=30, mode="workspace-write", isolation="required")
    assert result["stdout"].strip() == "confined workspace-write"
    assert result["sandbox"]["provider_id"] == "test-backend"
    assert [policy.mode for policy in backend.policies] == ["workspace-write"]
    assert backend.policies[0].workspace_root == str(tmp_path.resolve())


def test_an_unavailable_backend_falls_back_only_when_isolation_is_preferred(tmp_path: Path) -> None:
    bridge = CommandBridge(tmp_path, registry=with_unconfined_fallback([_Backend(available=False)]))
    result = bridge.run(command="echo hi", timeout=30, mode="read-only", isolation="preferred")
    assert result["sandbox"]["provider_id"] == "unconfined"
    with pytest.raises(UnsatisfiableRequirementError):
        bridge.run(command="echo hi", mode="read-only", isolation="required")


def test_danger_full_access_cannot_satisfy_required(tmp_path: Path) -> None:
    bridge = CommandBridge(tmp_path, registry=with_unconfined_fallback([_Backend()]))
    with pytest.raises(UnsatisfiableRequirementError):
        bridge.run(command="echo hi", mode="danger-full-access", isolation="required")
    result = bridge.run(command="echo hi", timeout=30, mode="danger-full-access", isolation="preferred")
    assert result["sandbox"]["provider_id"] == "unconfined"


def test_a_registry_without_a_fallback_refuses_what_it_cannot_confine(tmp_path: Path) -> None:
    bridge = CommandBridge(tmp_path, registry=ProviderRegistry([_Backend(available=False)]))
    with pytest.raises(UnsatisfiableRequirementError):
        bridge.run(command="echo hi", mode="read-only", isolation="preferred")


def test_a_mode_without_an_isolation_is_refused(tmp_path: Path) -> None:
    bridge = CommandBridge(tmp_path, registry=with_unconfined_fallback([_Backend()]))
    with pytest.raises(ProviderContractError, match="together"):
        bridge.run(command="echo hi", mode="read-only")
    with pytest.raises(ProviderContractError, match="together"):
        bridge.run(command="echo hi", isolation="required")


def test_no_requirement_runs_unconfined_and_says_so(tmp_path: Path) -> None:
    backend = _Backend()
    result = CommandBridge(tmp_path, registry=with_unconfined_fallback([backend])).run(command="echo hi", timeout=30)
    assert result["sandbox"]["provider_id"] == "unconfined"
    assert backend.policies == []


def test_a_fixed_provider_and_a_registry_together_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not both"):
        CommandBridge(
            tmp_path,
            provider=_Backend(),
            policy=ExecutionPolicy(mode="read-only", workspace_root=str(tmp_path)),
            registry=with_unconfined_fallback([]),
        )


# --- the composition root -------------------------------------------------------


def test_the_cli_offers_the_shipped_backends_then_the_fallback() -> None:
    from odessy.cli import execution_registry

    assert [p.provider_id for p in execution_registry().providers()] == ["bwrap", "landlock", "seatbelt", "windows-acl", "unconfined"]


def test_every_cli_tool_server_with_a_terminal_gets_the_registry() -> None:
    import inspect

    import odessy.cli as cli

    source = inspect.getsource(cli)
    calls = source.count("build_odessy_mcp_server(")
    with_registry = source.count("registry=execution_registry()")
    without_terminal = source.count("include_terminal=False")
    assert calls == with_registry + without_terminal


class _Pwd:
    def propose(self, user_text):
        return ToolRequest("terminal.run", {"command": "pwd", "timeout": 30})

    def respond_to_observation(self, user_text, observation):
        self.observation = observation
        return FinalResponse("done")


@pytest.mark.anyio
async def test_the_agent_sends_required_and_the_python_terminal_refuses(tmp_path: Path) -> None:
    model = _Pwd()
    agent = OdessyAgent(
        model=model,
        policy=OdessyPolicy(workspace_root=tmp_path, isolation="required"),
        mcp_server=ExecutionTarget(
            build_odessy_mcp_server(workspace_root=tmp_path, registry=with_unconfined_fallback([]))
        ),
    )
    await agent.run_turn("pwd")
    assert model.observation.result["status"] == "refused"


@pytest.mark.anyio
async def test_the_agent_runs_a_read_only_command_under_the_selected_backend(tmp_path: Path) -> None:
    model, backend = _Pwd(), _Backend()
    agent = OdessyAgent(
        model=model,
        policy=OdessyPolicy(workspace_root=tmp_path, isolation="required"),
        mcp_server=ExecutionTarget(
            build_odessy_mcp_server(workspace_root=tmp_path, registry=with_unconfined_fallback([backend]))
        ),
    )
    await agent.run_turn("pwd")
    assert model.observation.result["stdout"].strip() == "confined read-only"
    assert model.observation.result["sandbox"]["enforcement"] == "full"
