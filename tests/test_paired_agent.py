"""Agent-level coverage for a paired host: policy, approval and stale-trust refusal.

These run without a network: the transport is a fake that records calls, so what is
asserted here is the *Odessy* path — policy decisions, approval flow, and the
coordinator's re-check of its own trust state immediately before dispatch. The real
transport is covered separately in `test_paired_transport.py`.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from odessy.authority.policy import OdessyPolicy
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.runtime.types import FinalResponse, ToolRequest
from odessy.tools.external_tools import ServerConfig
from odessy.tools.paired import PairedTrustError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


class FakePairedServer:
    """What `PairedServer` looks like to the agent: a prefix, a config, a preflight."""

    def __init__(self, workspace: Path) -> None:
        self.prefix = ""
        self.config = ServerConfig(
            name="host",
            transport="stdio",
            command=("odessy-link", "connect", "--peer", "host"),
        )
        self.workspace = workspace
        self.calls: list[tuple[str, dict]] = []
        self.preflights = 0
        self.trust_changed = False

    def preflight(self) -> None:
        self.preflights += 1
        if self.trust_changed:
            raise PairedTrustError(
                "local permission revision changed (7 -> 8); a new approval is required"
            )

    async def list_tools(self):
        return [_Tool("laptop.terminal.run")]

    async def call_tool(self, name, arguments, *, before_dispatch=None):
        if before_dispatch is not None:
            before_dispatch()
        self.calls.append((name, dict(arguments)))
        # Shaped like a real MCP result: the agent reads structured content first.
        return SimpleNamespace(
            structured_content={"status": "ok", "text": "done"}, content=[], is_error=False
        )


class OneShotModel:
    def __init__(self, request: ToolRequest) -> None:
        self.request = request

    def begin_turn(self, **kwargs) -> None:
        self.deadline = kwargs.get("deadline")

    def propose(self, text: str) -> ToolRequest:
        return self.request

    def respond_to_observation(self, text: str, observation):
        return FinalResponse(text="finished")


def _agent(tmp_path: Path, server: FakePairedServer, request: ToolRequest, *, confirm):
    return OdessyAgent(
        model=OneShotModel(request),
        policy=OdessyPolicy(workspace_root=tmp_path),
        mcp_server=(ExecutionTarget(server),),
        confirm=confirm,
    )


async def test_an_allowed_paired_command_executes(tmp_path: Path):
    server = FakePairedServer(tmp_path)
    agent = _agent(
        tmp_path,
        server,
        ToolRequest("laptop.terminal.run", {"command": "pwd", "cwd": str(tmp_path)}),
        confirm=lambda decision, request: True,
    )
    result = await agent.run_turn("where am I")
    assert [name for name, _ in server.calls] == ["laptop.terminal.run"]
    assert server.preflights >= 1, "trust is re-read before dispatch"
    assert result.final_text == "finished"


async def test_a_denied_paired_command_never_executes(tmp_path: Path):
    server = FakePairedServer(tmp_path)
    agent = _agent(
        tmp_path,
        server,
        ToolRequest("laptop.terminal.run", {"command": "rm -rf build", "cwd": str(tmp_path)}),
        confirm=lambda decision, request: True,
    )
    with pytest.raises(PermissionError, match="destructive"):
        await agent.run_turn("delete the build directory")
    assert server.calls == [], "policy denial must not reach the peer"
    assert server.preflights == 0, "a denied request never reads trust either"


async def test_a_pending_approval_is_refused_after_the_local_revision_changes(tmp_path: Path):
    """The approval is granted, then this machine's permissions change: no execution."""

    server = FakePairedServer(tmp_path)
    asked: list[str] = []

    def confirm(decision, request):
        # The operator approves, then local trust moves before dispatch.
        asked.append(decision.action)
        server.trust_changed = True
        return True

    agent = _agent(
        tmp_path,
        server,
        ToolRequest("laptop.terminal.run", {"command": "mkdir build", "cwd": str(tmp_path)}),
        confirm=confirm,
    )
    with pytest.raises(PairedTrustError, match="permission revision changed"):
        await agent.run_turn("create the build directory")
    assert asked == ["confirm"], "the command must reach the approval path"
    assert server.calls == [], "a stale approval must not dispatch"


async def test_a_stale_approval_is_refused_when_the_peer_is_re_enrolled(tmp_path: Path):
    server = FakePairedServer(tmp_path)
    server.trust_changed = True
    agent = _agent(
        tmp_path,
        server,
        ToolRequest("laptop.terminal.run", {"command": "mkdir build", "cwd": str(tmp_path)}),
        confirm=lambda decision, request: True,
    )
    with pytest.raises(PairedTrustError):
        await agent.run_turn("create the build directory")
    assert server.calls == [], "re-enrolment invalidates the approval before dispatch"
