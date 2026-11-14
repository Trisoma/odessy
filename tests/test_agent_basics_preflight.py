"""Offline service-boundary gates for the Wave 2 shared seams."""
import asyncio
from types import SimpleNamespace

import httpx

from odessy.application.api import create_app
from odessy.application.session import SessionController
from odessy.authority.policy import OdessyPolicy
from odessy.runtime.types import AgentTurnResult, ToolObservation, ToolRequest


def test_explicit_binding_context_owner_and_durable_output_attachments(tmp_path):
    async def run():
        resource = asyncio.create_task(asyncio.Event().wait())
        seen = []
        class Agent:
            def __init__(self, *args, **kwargs):
                pass
            async def run_turn(self, text, **kwargs):
                seen.append(text)
                return AgentTurnResult('done', [], tool_observations=[ToolObservation(
                    ToolRequest('terminal.run', {'command': 'private argument'}),
                    {'status': 'ok', 'stdout': 'boat output', 'stderr': '', 'exit_code': 0},
                    call_id='fixture-command')])
        def factory():
            controller = SessionController(model_factory=lambda _: object(), agent_factory=Agent,
                                           profiles={'current': {}})
            controller.resources = SimpleNamespace(task=resource, closing=asyncio.Event())
            controller._workspace_root = tmp_path
            controller._workspace_policy_factory = lambda: OdessyPolicy(tmp_path)
            controller._workspace_authorized = lambda: True
            return controller
        app = create_app(factory, token='fixture', config_path=tmp_path / 'profile.env',
                         conversations_enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                    headers={'Authorization': 'Bearer fixture'}) as client:
            live = (await client.post('/v1/sessions')).json()
            pid = (await client.get('/v1/projects')).json()['projects'][0]['id']
            row = (await client.post('/v1/conversations', json={'project_id': pid})).json()
            assert (await client.post('/v1/conversations/' + row['id'] + '/resume',
                json={'expected_revision': row['revision']})).status_code == 409
            body = {'session_id': live['id'], 'service_instance_id': app.state.service_instance_id,
                    'expected_revision': row['revision']}
            stale = await client.post('/v1/conversations/' + row['id'] + '/bind-workspace',
                                      json={**body, 'service_instance_id': 'stale'})
            assert stale.status_code == 409
            bound = await client.post('/v1/conversations/' + row['id'] + '/bind-workspace', json=body)
            assert bound.status_code == 200, bound.text
            assert (await client.post('/v1/conversations/' + row['id'] + '/bind-workspace',
                                      json=body)).status_code == 409
            resumed = await client.post('/v1/conversations/' + row['id'] + '/resume',
                                       json={'expected_revision': bound.json()['revision']})
            assert resumed.status_code == 200, resumed.text
            sid = resumed.json()['session_id']
            owner = app.state.basics_composition.resolve_conversation_owner(row['id'])
            assert owner is app.state.sessions[sid]
            assert app.state.basics_composition.resolve_context_workspace(row['id']).session_id == sid
            import pytest
            from odessy.application.conversations.store import Conflict
            from odessy.application.agent_basics import PreparedContext
            composition = app.state.basics_composition
            with pytest.raises(Conflict):
                composition.resolve_project_workspace('f' * 32, sid)
            assert composition.resolve_project_workspace(pid, sid).session_id == sid
            class Oversized:
                def prepare(self, cid, **kwargs):
                    return PreparedContext(cid, 0, summary='x' * 24001)
            composition.context_provider = Oversized()
            with pytest.raises(Conflict):
                composition.prepare_context(app.state.sessions[sid], 'query')
            composition.context_provider = None
            (tmp_path / 'note.txt').write_text('reviewed boat', encoding='utf-8')
            file = (await client.get(f'/v1/sessions/{sid}/workspace/file', params={'path': 'note.txt'})).json()
            descriptor = {key: file[key] for key in ('path', 'sha256', 'service_instance_id', 'workspace_id')}
            denied = await client.post(f'/v1/sessions/{sid}/turns', json={
                'text': 'hello', 'attachments': [{**descriptor, 'sha256': '0' * 64}]})
            assert denied.status_code == 409
            assert seen == []
            accepted = await client.post(f'/v1/sessions/{sid}/turns', json={
                'text': 'hello', 'attachments': [descriptor]})
            assert accepted.status_code == 202, accepted.text
            await asyncio.gather(*app.state.turn_tasks.values())
            assert 'reviewed boat' in seen[0]
            events = app.state.conversations.store.raw_events(row['id'])
            assert any(e['kind'] == 'attachment' and e['payload']['name'] == 'note.txt' for e in events)
            outputs = [e for e in events if e['kind'] == 'tool_result' and 'output' in e['payload']]
            assert outputs[0]['payload']['output']['stdout'] == 'boat output'
            assert 'private argument' not in str(events)
            for value in list(app.state.sessions):
                await client.delete('/v1/sessions/' + value)
            assert app.state.basics_composition.service.store.export(row['id'])['events']
        resource.cancel()
        await asyncio.gather(resource, return_exceptions=True)
        app.state.conversations.store.close()
        from odessy.application.conversations import build_service
        restarted = build_service(path=tmp_path / 'conversations.sqlite3')
        durable = restarted.store.raw_events(row['id'])
        assert any(e['kind'] == 'attachment' for e in durable)
        assert any(e['payload'].get('output', {}).get('stdout') == 'boat output' for e in durable)
        restarted.store.close()
    asyncio.run(run())


def test_attached_batch_uses_per_call_barrier():
    from odessy.runtime.agent_loop import OdessyAgent
    async def forbidden(*args, **kwargs):
        raise AssertionError('Attached mode must not prompt before per-call review')
    agent = OdessyAgent(model=object(), policy=object(), mcp_server=(), confirm_batch=forbidden)
    agent._basics_session = object()
    assert asyncio.run(agent._ask_batch([object(), object()], {}, [])) == {}
