import asyncio
from pathlib import Path
import pytest
from odessy.runtime.types import ToolBatch, ToolCall, ToolRequest
from odessy.runtime.scheduler import schedule_calls

@pytest.mark.anyio
async def test_pool_overlaps_and_commits_in_order():
    entered = asyncio.Event()
    release = asyncio.Event()
    active = 0
    peak = 0
    async def prepare(call):
        return call.request.arguments
    async def dispatch(call, args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if call.call_id == 'a':
            entered.set()
            await release.wait()
        else:
            await entered.wait()
            release.set()
        active -= 1
        return {'status': 'ok', 'id': call.call_id}
    batch = ToolBatch(tuple(ToolCall(x, ToolRequest('filesystem.read', {'path': x})) for x in ('a','b')))
    observations, stopped = await schedule_calls(batch, limit=2, safe=lambda c: True, prepare=prepare, dispatch=dispatch)
    assert peak == 2
    assert [o.call_id for o in observations] == ['a','b']
    assert not stopped

@pytest.mark.anyio
async def test_barriers_and_rolling_limit():
    active = set()
    history = []
    async def prepare(call):
        if call.call_id == 'write':
            assert not active
        return {}
    async def dispatch(call, args):
        if call.call_id == 'after':
            assert 'write' in history
        active.add(call.call_id)
        assert len(active) <= 2
        await asyncio.sleep(0)
        active.remove(call.call_id)
        history.append(call.call_id)
        return {'status': 'ok'}
    batch = ToolBatch(tuple(ToolCall(x, ToolRequest('time.now')) for x in ['a','b','c','write','after']))
    observations, reason = await schedule_calls(batch, limit=2, safe=lambda c: c.call_id != 'write', prepare=prepare, dispatch=dispatch)
    assert not reason
    assert len(observations) == 5
    assert history.index('write') > max(history.index(x) for x in ['a','b','c'])

@pytest.mark.anyio
async def test_stop_drains_started_and_skips_later():
    stop = asyncio.Event()
    async def prepare(call):
        return {}
    async def dispatch(call, args):
        stop.set()
        await asyncio.sleep(0)
        return {'status': 'ok'}
    batch = ToolBatch(tuple(ToolCall(x, ToolRequest('time.now')) for x in ['a','b','c']))
    obs, reason = await schedule_calls(batch, limit=1, safe=lambda c: True, prepare=prepare, dispatch=dispatch, stop=stop)
    assert reason
    assert obs[0].result['status'] == 'ok'
    assert [o.result['status'] for o in obs[1:]] == ['skipped','skipped']

@pytest.mark.anyio
async def test_unknown_remote_completion_is_honest():
    stop = asyncio.Event()
    async def prepare(call):
        return {}
    async def dispatch(call, args):
        stop.set()
        await asyncio.Event().wait()
    obs, reason = await schedule_calls(ToolBatch((ToolCall('a', ToolRequest('time.now')),)), limit=1, safe=lambda c: True, prepare=prepare, dispatch=dispatch, stop=stop, drain_seconds=0)
    assert reason
    assert obs[0].result['status'] == 'unknown'

@pytest.mark.anyio
async def test_denial_stops_later_calls():
    ran = []
    async def prepare(call):
        if call.call_id == 'deny':
            raise PermissionError('no')
        return {}
    async def dispatch(call, args):
        ran.append(call.call_id)
        return {'status': 'ok'}
    batch = ToolBatch(tuple(ToolCall(x, ToolRequest('time.now')) for x in ['a','deny','after']))
    obs, reason = await schedule_calls(batch, limit=1, safe=lambda c: False, prepare=prepare, dispatch=dispatch)
    assert ran == ['a']
    assert reason == 'no'
    assert [o.result['status'] for o in obs] == ['ok','denied','skipped']

@pytest.mark.anyio
async def test_runtime_batch_uses_policy_and_mcp(tmp_path):
    from odessy.runtime.agent_loop import OdessyAgent, ExecutionTarget
    from odessy.runtime.types import FinalResponse
    from odessy.authority.policy import OdessyPolicy
    from odessy.tools.mcp_server import build_odessy_mcp_server
    class Model:
        def propose(self, text):
            return ToolBatch((ToolCall('a', ToolRequest('time.now')), ToolCall('b', ToolRequest('calculator.eval', {'expression': '1+2'}))))
        def respond_to_observations(self, text, observations):
            assert [o.call_id for o in observations] == ['a','b']
            assert observations[1].result['result'] == 3
            return FinalResponse('done')
    agent = OdessyAgent(model=Model(), policy=OdessyPolicy(workspace_root=tmp_path), mcp_server=ExecutionTarget(build_odessy_mcp_server()), tool_concurrency=2)
    result = await agent.run_turn('time and calculate')
    assert result.final_text == 'done'
    assert len(result.tool_observations) == 2
    assert result.tool_request.name == 'calculator.eval'

@pytest.mark.anyio
async def test_preflight_denial_never_discovers(tmp_path):
    from odessy.runtime.agent_loop import OdessyAgent
    from odessy.runtime.types import FinalResponse
    from odessy.authority.policy import OdessyPolicy
    class Model:
        def propose(self, text):
            return ToolBatch((ToolCall('a', ToolRequest('time.now')), ToolCall('b', ToolRequest('filesystem.read', {'path': '.env'}))))
        def respond_to_observations(self, *args):
            raise AssertionError('must not retry denial')
    agent = OdessyAgent(model=Model(), policy=OdessyPolicy(workspace_root=tmp_path), mcp_server=None)
    async def explode():
        raise AssertionError('must not discover')
    agent._collect_tool_registry = explode
    result = await agent.run_turn('read secret')
    assert len(result.tool_observations) == 2
    assert 'denied' in result.final_text

@pytest.mark.parametrize('count', [0,17])
def test_batch_size_limits(count):
    with pytest.raises(ValueError):
        ToolBatch(tuple(ToolCall(str(i), ToolRequest('time.now')) for i in range(count)))

def test_duplicate_call_ids_rejected():
    with pytest.raises(ValueError):
        ToolBatch((ToolCall('x', ToolRequest('time.now')), ToolCall('x', ToolRequest('time.now'))))

@pytest.mark.parametrize('raw', ['0','9','bad'])
def test_invalid_concurrency(raw, monkeypatch):
    from odessy.runtime.scheduler import concurrency_from_env
    monkeypatch.setenv('ODESSY_TOOL_CONCURRENCY', raw)
    with pytest.raises(ValueError):
        concurrency_from_env()

def test_local_parser_preserves_batch():
    from odessy.models.ollama_model import _extract_model_action_from_chat
    action = _extract_model_action_from_chat({'message': {'tool_calls': [{'function': {'name': 'time.now', 'arguments': {}}}, {'function': {'name': 'calculator.eval', 'arguments': {'expression':'1+1'}}}]}}, 'time and calculate')
    assert isinstance(action, ToolBatch)
    assert [c.call_id for c in action.calls] == ['local-0','local-1']

@pytest.mark.anyio
async def test_stop_during_prepare_does_not_dispatch():
    stop = asyncio.Event()
    async def prepare(call):
        stop.set()
        return {}
    async def dispatch(*args):
        raise AssertionError('stopped calls cannot dispatch')
    obs, reason = await schedule_calls(ToolBatch((ToolCall('a', ToolRequest('time.now')),)), limit=2, safe=lambda c: True, prepare=prepare, dispatch=dispatch, stop=stop)
    assert reason
    assert obs[0].result['status'] == 'skipped'

@pytest.mark.anyio
async def test_task_cancellation_returns_terminal_observations():
    entered = asyncio.Event()
    release = asyncio.Event()
    async def prepare(call):
        return {}
    async def dispatch(*args):
        entered.set()
        await release.wait()
        return {'status': 'ok'}
    task = asyncio.create_task(schedule_calls(ToolBatch((ToolCall('a', ToolRequest('time.now')),)), limit=2, safe=lambda c: True, prepare=prepare, dispatch=dispatch))
    await entered.wait()
    task.cancel()
    release.set()
    obs, reason = await task
    assert reason == 'turn cancelled'
    assert obs[0].result['status'] == 'ok'
