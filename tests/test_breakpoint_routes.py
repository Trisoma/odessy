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

    asyncio.run(run())
