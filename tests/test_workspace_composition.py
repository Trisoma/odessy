"""Authenticated production router with an explicit, temporary local owner."""
import asyncio
from types import SimpleNamespace
import pytest
import httpx
from odessy.application.api import create_app
from odessy.authority.policy import OdessyPolicy

@pytest.mark.parametrize('engine', ['legacy', 'harness'])
def test_actual_router_owner_auth_revocation_and_root_replacement(tmp_path, engine):
    async def run():
        root = tmp_path / 'workspace'
        root.mkdir()
        (root / 'readme.txt').write_text('local fixture', encoding='utf-8')
        closing = asyncio.Event()
        task = asyncio.create_task(closing.wait())
        owner = SimpleNamespace(id='sid', engine=engine, _closed=False,
            resources=SimpleNamespace(task=task, closing=closing),
            _workspace_root=root, _workspace_authorized=lambda: True,
            _workspace_policy_factory=lambda: OdessyPolicy(root))
        app = create_app(lambda: None, token='fixture')
        app.state.sessions['sid'] = owner
        app.state.sessions['unowned'] = SimpleNamespace()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            url = '/v1/sessions/sid/workspace'
            assert (await client.get(url)).status_code == 401
            client.headers['Authorization'] = 'Bearer fixture'
            descriptor = (await client.get(url)).json()
            assert descriptor['service_instance_id'] == app.state.service_instance_id
            assert descriptor['capabilities']['proposed_changes'] == (engine == 'legacy')
            assert (await client.get(url + '/file?path=readme.txt')).json()['text'] == 'local fixture'
            assert (await client.get(url + '/file?path=../outside')).status_code == 403
            assert (await client.get('/v1/sessions/unowned/workspace')).status_code == 503
            assert (await client.get('/v1/sessions/missing/workspace')).status_code == 404
            owner._closed = True
            assert (await client.get(url)).status_code == 403
            owner._closed = False
            root.rename(tmp_path / 'previous')
            root.mkdir()
            assert (await client.get(url)).status_code == 409
            del app.state.sessions['sid']
            assert (await client.get(url)).status_code == 404
        closing.set()
        await task
    asyncio.run(run())
