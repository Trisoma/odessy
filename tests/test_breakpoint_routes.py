"""Breakpoint routes are mounted and expose the timeline through the app."""
import asyncio
from types import SimpleNamespace

import httpx

from odessy.application.api import create_app
from odessy.application.session import SessionController
from odessy.runtime.types import AgentTurnResult


def test_breakpoint_routes_mounted(tmp_path):
    class Agent:
        def __init__(self, *args, **kwargs):
            pass

        async def run_turn(self, text, **kwargs):
            return AgentTurnResult('answer', [])

    async def run():
        resource = asyncio.create_task(asyncio.Event().wait())
        root = tmp_path / 'workspace'
        root.mkdir()

        def factory():
            c = SessionController(model_factory=lambda _: object(), agent_factory=Agent,
                                  profiles={'current': {}})
            c.resources = SimpleNamespace(task=resource, closing=asyncio.Event())
            return c

        def application():
            return create_app(factory, token='bp-test',
                              config_path=tmp_path / 'profile.env', conversations_enabled=True)

        headers = {'Authorization': 'Bearer bp-test'}
        app = application()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                    base_url='http://test', headers=headers) as client:
                caps = (await client.get('/v1/agent-basics/capabilities')).json()
                assert caps.get('breakpoints') is True and caps.get('undo') is True
                live = (await client.post('/v1/sessions')).json()
                cid = live['conversation_id']
                # No breakpoints recorded yet: empty timeline, not a 404.
                resp = await client.get(f'/v1/conversations/{cid}/breakpoints')
                assert resp.status_code == 200
                assert resp.json()['breakpoints'] == []
                # Undo rejects a malformed anchor with a 400, not a crash.
                bad = await client.post(f'/v1/conversations/{cid}/undo',
                                        json={'breakpoint_id': 'bad', 'expected_revision': 0})
                assert bad.status_code == 400

                basics = app.state.basics_composition
                sid = live['id']
                assert (await client.post(f'/v1/sessions/{sid}/turns',
                                          json={'text': 'test turn'})).status_code == 202
                await asyncio.gather(*app.state.turn_tasks.values())
                store = basics.service.store
                timeline = store.breakpoints(cid)
                anchor = timeline[0]['payload']
                turn_id = anchor['task']['turn_id']
                assert turn_id and store.event_by_id(cid, f'{turn_id}:user') is not None
                revision = store.get(cid)['revision']
                undo_path = f'/v1/conversations/{cid}/undo'
                undo = {'breakpoint_id': anchor['id'], 'expected_revision': revision}
                stale = await client.post(undo_path, json={**undo, 'expected_revision': revision - 1})
                assert stale.status_code == 409
                other = (await client.post('/v1/sessions')).json()
                wrong_owner = await client.post(undo_path, json={**undo, 'session_id': other['id']})
                assert wrong_owner.status_code == 409
                assert not any(e['kind'] == 'undo' for e in store.raw_events(cid))
                saved = await client.post(undo_path, json={**undo, 'session_id': live['id']})
                assert saved.status_code == 200
                assert saved.json()['file_rollback']['status'] == 'not_requested'

    asyncio.run(run())
