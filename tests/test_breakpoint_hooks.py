"""Agent-loop breakpoint hooks fire on critical tools and step boundaries.

The runtime stays independent of the application: it only calls duck-typed
methods on a hook attached to ``_basics_session``. These tests attach a recording
hook to verify the dispatch points without pulling in SQLite or the composition.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from odessy.application.breakpoints import is_critical
from odessy.authority.policy import OdessyPolicy
from odessy.models.fake_model import FakeModel
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.tools.mcp_server import build_odessy_mcp_server


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class RecordingHook:
    def __init__(self):
        self.calls: list[tuple] = []

    def before_tool(self, **kwargs):
        if is_critical(kwargs.get('name'), kwargs.get('arguments'), kwargs.get('decision')):
            self.calls.append(('before', kwargs.get('name'), kwargs.get('call_id')))

    def after_tool(self, **kwargs):
        if is_critical(kwargs.get('name'), kwargs.get('arguments'), kwargs.get('decision')):
            self.calls.append(('after', kwargs.get('name'), kwargs.get('status')))

    def step(self, **kwargs):
        self.calls.append(('step',))


def _agent(tmp_path, *, hook=None):
    agent = OdessyAgent(
        model=FakeModel(),
        policy=OdessyPolicy(workspace_root=tmp_path,
                            allowed_tools={'terminal.run', 'calculator.eval'}),
        mcp_server=ExecutionTarget(build_odessy_mcp_server(workspace_root=tmp_path)),
    )
    if hook is not None:
        # Only the agent-loop side is set; no _conversation_recorder, no access
        # modes, so this exercises the hook seam in isolation.
        inner = getattr(agent, 'agent', agent)
        inner._basics_session = type('Basics', (), {'_breakpoint_hook': hook})()
    return agent


@pytest.mark.anyio
async def test_critical_tool_gets_before_after_and_step(tmp_path):
    hook = RecordingHook()
    agent = _agent(tmp_path, hook=hook)
    response = await agent.run_turn('run echo hi')
    assert response.metrics.tool_called is True
    kinds = [c[0] for c in hook.calls]
    # terminal.run is critical: one before, one after, one step for the iteration.
    assert 'before' in kinds and 'after' in kinds and 'step' in kinds
    before = [c for c in hook.calls if c[0] == 'before']
    after = [c for c in hook.calls if c[0] == 'after']
    assert before[0][1] == 'terminal.run'
    assert after[0][1] == 'terminal.run' and after[0][2] == 'ok'


@pytest.mark.anyio
async def test_noncritical_tool_still_gets_step_but_no_before_after(tmp_path):
    hook = RecordingHook()
    agent = _agent(tmp_path, hook=hook)
    await agent.run_turn('calculate 2 + 3')
    kinds = [c[0] for c in hook.calls]
    # calculator.eval is low-risk and not on the side-effect whitelist:
    # no before/after, but a step breakpoint still marks the iteration.
    assert 'before' not in kinds and 'after' not in kinds
    assert 'step' in kinds


@pytest.mark.anyio
async def test_no_hook_is_a_noop(tmp_path):
    agent = _agent(tmp_path)  # no hook attached
    response = await agent.run_turn('run echo hi')
    assert response.final_text is not None  # turn succeeds; hooks are optional
