"""Combined production mounts, real broker and deterministic provider acceptance."""
import asyncio
import subprocess
from types import SimpleNamespace

import httpx

from odessy.application.api import create_app
from odessy.application.session import SessionController
from odessy.authority.policy import OdessyPolicy
from odessy.runtime.types import AgentTurnResult


def test_production_mounts_restart_memory_citations_and_revocation(tmp_path):
    seen = []
    allowed = [True]

    class Agent:
        def __init__(self, *args, **kwargs):
            pass

        async def run_turn(self, text, **kwargs):
            seen.append((text, kwargs['context']))
            return AgentTurnResult('deterministic boat answer', [])

    async def run():
        resource = asyncio.create_task(asyncio.Event().wait())
        root = tmp_path / 'workspace'
        root.mkdir()
        (root / 'boat.md').write_text('The boat is called Aurora.', encoding='utf-8')

        def factory():
            c = SessionController(model_factory=lambda _: object(), agent_factory=Agent,
                                  profiles={'current': {}})
            c.resources = SimpleNamespace(task=resource, closing=asyncio.Event())
            c._workspace_root = root
            c._workspace_policy_factory = lambda: OdessyPolicy(root)
            c._workspace_authorized = lambda: allowed[0]
            return c

        def application():
            return create_app(factory, token='production-test',
                              config_path=tmp_path / 'profile.env', conversations_enabled=True)

        headers = {'Authorization': 'Bearer production-test'}
        app = application()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url='http://test', headers=headers) as client:
                caps = (await client.get('/v1/agent-basics/capabilities')).json()
                assert all(caps[name] for name in ('context', 'memory', 'retrieval', 'checkpoints'))
                live = (await client.post('/v1/sessions')).json()
                sid, cid = live['id'], live['conversation_id']
                row = (await client.get('/v1/conversations/' + cid)).json()
                pid = row['project_id']
                fact = (await client.post(f'/v1/projects/{pid}/memory',
                                         json={'text': 'Prefer blue boats.'})).json()
                indexed = await client.post(f'/v1/projects/{pid}/documents', json={
                    'session_id': sid, 'service_instance_id': caps['service_instance_id'],
                    'path': 'boat.md'})
                assert indexed.status_code == 201, indexed.text
                assert (await client.post(f'/v1/sessions/{sid}/turns',
                                         json={'text': 'boat'})).status_code == 202
                await asyncio.gather(*app.state.turn_tasks.values())
                assert 'Prefer blue boats.' in str(seen[-1][1])
                assert 'Aurora' in str(seen[-1][1])
                used = (await client.get(f'/v1/conversations/{cid}/context')).json()
                assert used['memory_ids'] == [fact['id']] and used['citations']
                changed = await client.patch(f"/v1/projects/{pid}/memory/{fact['id']}",
                    json={'expected_revision': fact['revision'], 'enabled': False})
                assert changed.status_code == 200, changed.text
                (root / 'boat.md').write_text('The boat is now changed.', encoding='utf-8')
                await client.post(f'/v1/sessions/{sid}/turns', json={'text': 'What boat now?'})
                await asyncio.gather(*app.state.turn_tasks.values())
                assert 'Prefer blue boats.' not in str(seen[-1][1])
                assert 'Aurora' not in str(seen[-1][1])
                row = (await client.get('/v1/conversations/' + cid)).json()
        # Recreate the application and every live owner on the same isolated profile.
        other = application()
        async with other.router.lifespan_context(other):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=other),
                    base_url='http://test', headers=headers) as client:
                assert other.state.sessions == {}
                response = await client.post(f'/v1/conversations/{cid}/resume',
                                             json={'expected_revision': row['revision']})
                assert response.status_code == 200, response.text
                sid = response.json()['session_id']
                count = len(seen)
                await client.post(f'/v1/sessions/{sid}/turns', json={'text': 'Continue boat'})
                await asyncio.gather(*other.state.turn_tasks.values())
                assert len(seen) == count + 1  # History never replays model/tool work.
                assert any(t.user == 'boat' for t in seen[-1][1].turns)
                allowed[0] = False
                rejected = await client.get(f'/v1/sessions/{sid}/workspace/tree')
                assert rejected.status_code == 403
        resource.cancel()
        await asyncio.gather(resource, return_exceptions=True)
    asyncio.run(run())


def test_direct_shell_grant_respects_required_isolation_and_exact_broker(tmp_path):
    from dataclasses import replace

    async def run():
        resource = asyncio.create_task(asyncio.Event().wait())
        isolation = ['required']
        subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
        (tmp_path / 'boat.txt').write_text('original boat', encoding='utf-8')
        subprocess.run(['git', '-C', str(tmp_path), 'add', 'boat.txt'], check=True)
        subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=Fixture',
                        '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'fixture'], check=True)
        def factory():
            c = SessionController(model_factory=lambda _: object(), agent_factory=lambda *a, **kw: None,
                                  profiles={'current': {}})
            c.resources = SimpleNamespace(task=resource, closing=asyncio.Event())
            c._workspace_root = tmp_path
            c._workspace_authorized = lambda: True
            c._workspace_policy_factory = lambda: replace(OdessyPolicy(tmp_path), isolation=isolation[0])
            return c
        app = create_app(factory, token='production-test', config_path=tmp_path / 'profile.env',
                         conversations_enabled=True)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url='http://test', headers={'Authorization': 'Bearer production-test'}) as client:
                sid = (await client.post('/v1/sessions')).json()['id']
                composition = app.state.basics_composition
                plan = {'action': 'terminal.create', 'disclosure': 'Unconfined OS-user shell.'}
                assert await composition.request_grant(sid, plan) is False
                assert not app.state.sessions[sid].interactions.pending
                isolation[0] = 'preferred'
                grant = asyncio.create_task(composition.request_grant(sid, plan))
                for _ in range(100):
                    pending = app.state.sessions[sid].interactions.pending
                    if pending:
                        break
                    await asyncio.sleep(.01)
                assert pending[0]['scopes'] == ['sandbox_escalation']
                response = await client.post(f"/v1/sessions/{sid}/approvals/{pending[0]['id']}",
                                             json={'approved': True})
                assert response.status_code == 200, response.text
                assert await grant is True
                assert (await client.post(f"/v1/sessions/{sid}/approvals/{pending[0]['id']}",
                    json={'approved': True})).status_code in (404, 409)
                async def approve_post(path, body):
                    operation = asyncio.create_task(client.post(path, json=body))
                    for _ in range(200):
                        entries = app.state.sessions[sid].interactions.pending
                        if entries or operation.done():
                            break
                        await asyncio.sleep(.01)
                    assert entries, (await operation).text if operation.done() else 'No approval'
                    answer = await client.post(f"/v1/sessions/{sid}/approvals/{entries[0]['id']}",
                                               json={'approved': True})
                    assert answer.status_code == 200, answer.text
                    return await operation

                created = await approve_post(f'/v1/sessions/{sid}/checkpoints', {'label': 'Boat before'})
                assert created.status_code == 201, created.text
                checkpoint = created.json()
                screened = composition.checkpoints.restore_policy_requests(sid, checkpoint['id'])
                assert any(r['path'] == 'boat.txt' and r['content'] == 'original boat' for r in screened)
                (tmp_path / 'boat.txt').write_text('edited boat', encoding='utf-8')
                (tmp_path / 'unrelated.txt').write_text('keep dirty work', encoding='utf-8')
                preview = (await client.get(f"/v1/sessions/{sid}/checkpoints/{checkpoint['id']}/preview")).json()
                restored = await approve_post(f"/v1/sessions/{sid}/checkpoints/{checkpoint['id']}/restore",
                    {'preview_hash': preview['preview_hash'], 'expected_revision': preview['expected_revision']})
                assert restored.status_code == 200, restored.text
                assert (tmp_path / 'boat.txt').read_text() == 'original boat'
                assert (tmp_path / 'unrelated.txt').read_text() == 'keep dirty work'
                assert restored.json()['safety_checkpoint_id']
                if composition.terminals is not None:
                    created = await approve_post(f'/v1/sessions/{sid}/terminals', {'columns': 80, 'rows': 24})
                    assert created.status_code == 201, created.text
                    handle = created.json()
                    tid = handle['id']
                    sent = await client.post(f'/v1/sessions/{sid}/terminals/{tid}/input',
                        json={'text': 'echo ODESSY_07_REAL_SHELL\r', 'expected_revision': handle['revision']})
                    assert sent.status_code == 204, sent.text
                    for _ in range(100):
                        output = (await client.get(f'/v1/sessions/{sid}/terminals/{tid}/output')).json()
                        if 'ODESSY_07_REAL_SHELL' in output['output']:
                            break
                        await asyncio.sleep(.03)
                    assert 'ODESSY_07_REAL_SHELL' in output['output']
                    await client.delete('/v1/sessions/' + sid)
                    assert composition.terminals.handles[tid]['process'].returncode is not None
        resource.cancel()
        await asyncio.gather(resource, return_exceptions=True)
    asyncio.run(run())
