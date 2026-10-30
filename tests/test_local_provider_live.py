"""Opt-in local real model, real session and agent; no tool targets or cloud."""
import asyncio
import json
import os
from urllib.parse import urlsplit

import httpx
import pytest

from odessy.application.api import create_app
from odessy.application.session import SessionController
from odessy.authority.policy import OdessyPolicy
from odessy.models.ollama_model import OllamaModel
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent

pytestmark = pytest.mark.skipif(
    os.environ.get('ODESSY_LOCAL_PROVIDER_E2E') != '1',
    reason='requires an explicitly started isolated local model')


def test_local_model_session_selection_stop_and_failure(tmp_path, monkeypatch):
    endpoint = os.environ['ODESSY_TEST_OLLAMA_ENDPOINT']
    parsed = urlsplit(endpoint)
    assert parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')
    monkeypatch.setenv('ODESSY_MONITORING', 'off')
    monkeypatch.setenv('ODESSY_CONFIG_FILE', str(tmp_path / 'empty.env'))
    profiles = {'local-a': {}, 'local-b': {}, 'missing-model': {}}

    def model_factory(name):
        model = 'odessy-missing-fixture-model' if name == 'missing-model' else 'qwen2.5:0.5b'
        return OllamaModel(model=model, endpoint=endpoint, text_only=True,
                           num_predict=128, num_ctx=2048, timeout=60,
                           call_timeout=120, keep_alive=0)

    def agent_factory(model, confirm=None):
        return OdessyAgent(model=model, policy=OdessyPolicy(workspace_root=tmp_path),
                           mcp_server=(), confirm=False, max_model_calls=1)

    def factory():
        return SessionController(model_factory=model_factory, agent_factory=agent_factory,
                                 profiles=profiles, selected_profile='local-a')

    async def run():
        app = create_app(factory, token='isolated-provider-fixture', profiles=profiles)
        headers = {'Authorization': 'Bearer isolated-provider-fixture'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://local') as client:
            assert (await client.get('/v1/profiles')).status_code == 401
            created = await client.post('/v1/sessions', headers=headers)
            assert created.status_code == 201
            sid = created.json()['id']
            path = f'/v1/sessions/{sid}'

            async def snapshot():
                response = await client.get(path, headers=headers)
                assert response.status_code == 200
                return response.json()

            async def wait_for_event(event_type, after):
                for _ in range(600):
                    value = await snapshot()
                    if any(event['type'] == event_type and event['id'] > after
                           for event in value['events']):
                        return value
                    await asyncio.sleep(.1)
                pytest.fail(f'{event_type} did not arrive')

            try:
                selected = await client.put(path + '/model', headers=headers,
                                            json={'profile': 'local-b'})
                assert selected.status_code == 200
                sent = await client.post(path + '/turns', headers=headers,
                    json={'text': 'Hello! Reply with one short English greeting.'})
                assert sent.status_code == 202
                value = await wait_for_event('result', 0)
                assert value['selected_profile'] == 'local-b'
                assert value['history'] and value['history'][-1]['assistant'].strip()
                assert any(event['type'] == 'selection' and
                           event['data'].get('profile') == 'local-b'
                           for event in value['events'])
                last = max(event['id'] for event in value['events'])
                history_count = len(value['history'])

                sent = await client.post(path + '/turns', headers=headers,
                    json={'text': 'Rewrite this paragraph in detail: ' + 'hello world. ' * 80})
                assert sent.status_code == 202
                running = await snapshot()
                assert running['running'], 'model finished before Stop could be exercised'
                stopped = await client.post(path + '/stop', headers=headers, timeout=65)
                assert stopped.status_code == 200
                value = await wait_for_event('stopped', last)
                assert not value['running']
                assert len(value['history']) == history_count
                last = max(event['id'] for event in value['events'])

                selected = await client.put(path + '/model', headers=headers,
                                            json={'profile': 'missing-model'})
                assert selected.status_code == 200
                sent = await client.post(path + '/turns', headers=headers,
                                         json={'text': 'Hello again.'})
                assert sent.status_code == 202
                value = await wait_for_event('error', last)
                assert len(value['history']) == history_count
                assert 'isolated-provider-fixture' not in json.dumps(value)
            finally:
                await client.delete(path, headers=headers)
    asyncio.run(run())


def test_local_model_approval_denial_never_dispatches(tmp_path, monkeypatch):
    from mcp.server import MCPServer

    endpoint = os.environ['ODESSY_TEST_OLLAMA_ENDPOINT']
    parsed = urlsplit(endpoint)
    assert parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')
    monkeypatch.setenv('ODESSY_MONITORING', 'off')
    calls = []
    tool_server = MCPServer('isolated-tool-stub')

    @tool_server.tool(name='terminal.run')
    def terminal_stub(command: str, cwd: str = '.', timeout: int = 10) -> dict:
        calls.append(command)
        raise AssertionError('Fixture tool must never execute')

    def factory():
        def model_factory(name):
            return OllamaModel(model='qwen2.5:0.5b', endpoint=endpoint,
                               num_predict=128, num_ctx=4096, timeout=60,
                               call_timeout=120, keep_alive=0)

        def agent_factory(model, confirm=None):
            return OdessyAgent(model=model, policy=OdessyPolicy(workspace_root=tmp_path),
                mcp_server=(ExecutionTarget(tool_server),), confirm=False,
                confirm_action=controller.confirm_action, max_model_calls=2)

        controller = SessionController(model_factory=model_factory, agent_factory=agent_factory,
                                       profiles={'tool-local': {}}, selected_profile='tool-local')
        return controller

    async def run():
        app = create_app(factory, token='isolated-provider-fixture', profiles={'tool-local': {}})
        headers = {'Authorization': 'Bearer isolated-provider-fixture'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://local') as client:
            created = await client.post('/v1/sessions', headers=headers)
            assert created.status_code == 201
            path = '/v1/sessions/' + created.json()['id']
            try:
                response = await client.post(path + '/turns', headers=headers,
                    json={'text': 'Use terminal.run to run mkdir fixture-folder. Do not assume it succeeded.'})
                assert response.status_code == 202
                for _ in range(600):
                    snap = (await client.get(path, headers=headers)).json()
                    if snap['pending_approvals']:
                        break
                    if any(event['type'] in ('result', 'error') for event in snap['events']):
                        pytest.fail('real model did not produce an approval request')
                    await asyncio.sleep(.1)
                else:
                    pytest.fail('approval did not arrive')
                pending = snap['pending_approvals'][0]
                assert pending['operation'] == 'terminal.run'
                assert pending['arguments']['command'] == 'mkdir fixture-folder'
                reply = path + '/approvals/' + pending['id']
                assert (await client.post(reply, headers=headers, json={'approved': False})).status_code == 200
                for _ in range(600):
                    snap = (await client.get(path, headers=headers)).json()
                    if not snap['running']:
                        break
                    await asyncio.sleep(.1)
                assert not snap['running']
                duplicate = await client.post(reply, headers=headers, json={'approved': True})
                assert duplicate.status_code in (404, 409, 410)
                assert not calls and not (tmp_path / 'fixture-folder').exists()
            finally:
                await client.delete(path, headers=headers)
    asyncio.run(run())
