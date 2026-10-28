import asyncio
from contextlib import asynccontextmanager
import pytest
from odessy import cli
from odessy.application.bootstrap import create_controller
from odessy.models.fake_model import FakeModel
from odessy.runtime.types import FinalResponse
from odessy.config.deployment import model_tool_names

class ChatModel(FakeModel):
    def propose(self, text, *, context=None):
        assert model_tool_names() == []
        return FinalResponse('Hello from the selected model')

@pytest.mark.parametrize('failure', [RuntimeError('offline'), SystemExit('revoked')])
def test_executor_failure_allows_chat_but_no_tools_hooks_or_jobs(monkeypatch, tmp_path, failure):
    monkeypatch.setenv('ODESSY_PAIRED_HOST', 'laptop')
    monkeypatch.setenv('ODESSY_DEPLOYMENT', 'split')
    @asynccontextmanager
    async def unavailable(*args, **kwargs):
        raise failure
        yield
    monkeypatch.setattr(cli, '_tool_servers', unavailable)
    def forbidden(*args, **kwargs):
        raise AssertionError('normal tool/hook agent must not be constructed')
    monkeypatch.setattr(cli, '_build_agent', forbidden)
    async def run():
        model = ChatModel()
        controller = await create_controller(workspace=tmp_path, model=model)
        try:
            assert controller.jobs is None
            assert controller.collaboration is None
            assert any(e['type'] == 'execution_unavailable' for e in controller.events)
            result = await controller.run_turn('Hi')
            assert result.final_text == 'Hello from the selected model'
            wrapper = controller.agent_factory(model)
            agent = wrapper.agent
            assert agent._mcp_servers == ()
            assert agent._hooks is None
            assert (await wrapper.run_turn('run echo forbidden')).tool_result is None
        finally:
            await controller.close()
    asyncio.run(run())

def test_hallucinated_tool_never_runs(monkeypatch, tmp_path):
    monkeypatch.setenv('ODESSY_PAIRED_HOST', 'laptop')
    @asynccontextmanager
    async def unavailable(*args, **kwargs):
        raise RuntimeError('offline')
        yield
    monkeypatch.setattr(cli, '_tool_servers', unavailable)
    async def run():
        controller = await create_controller(workspace=tmp_path, model=FakeModel())
        try:
            result = await controller.run_turn('run touch should-not-exist')
            assert not (tmp_path / 'should-not-exist').exists()
            assert result.tool_result is None
            assert not controller.pending_approvals
        finally:
            await controller.close()
    asyncio.run(run())


def test_slow_paired_initialization_is_bounded_and_closed(monkeypatch, tmp_path):
    monkeypatch.setenv('ODESSY_PAIRED_HOST', 'laptop')
    closed = []
    @asynccontextmanager
    async def slow(*args, **kwargs):
        try:
            await asyncio.sleep(60)
            yield ()
        finally:
            closed.append(True)
    monkeypatch.setattr(cli, '_tool_servers', slow)
    async def run():
        controller = await asyncio.wait_for(create_controller(workspace=tmp_path, model=ChatModel()), 6)
        try:
            assert closed == [True]
            assert (await controller.run_turn('Hi')).final_text == 'Hello from the selected model'
        finally:
            await controller.close()
    asyncio.run(run())


def test_http_chat_remains_available_and_jobs_are_refused(monkeypatch, tmp_path):
    import httpx
    from odessy.application.api import create_app
    monkeypatch.setenv('ODESSY_PAIRED_HOST', 'laptop')
    @asynccontextmanager
    async def unavailable(*args, **kwargs):
        raise RuntimeError('executor disconnected')
        yield
    monkeypatch.setattr(cli, '_tool_servers', unavailable)
    async def run():
        async def factory():
            return await create_controller(workspace=tmp_path, model=ChatModel())
        app = create_app(factory, token='test', profiles={'current': {}})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'Authorization': 'Bearer test'}) as client:
            response = await client.post('/v1/sessions')
            assert response.status_code == 201
            sid = response.json()['id']
            assert (await client.post(f'/v1/sessions/{sid}/turns', json={'text': 'Hi'})).status_code == 202
            for _ in range(100):
                await asyncio.sleep(.01)
                if not app.state.sessions[sid].running:
                    break
            state = (await client.get(f'/v1/sessions/{sid}')).json()
            assert state['history'][-1]['assistant'] == 'Hello from the selected model'
            response = await client.post(f'/v1/sessions/{sid}/jobs', json={'participant': 'agent', 'task': 'run command'})
            assert response.status_code == 503
            await client.delete(f'/v1/sessions/{sid}')
    asyncio.run(run())
