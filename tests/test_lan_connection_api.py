import asyncio
import httpx

from odessy.application.api import create_app
from odessy.application import lan_connections


def test_execution_host_commands_are_authenticated_and_model_independent(monkeypatch):
    async def run():
        def factory():
            raise AssertionError('No model/session is needed to configure execution')
        app = create_app(factory, token='local-secret')
        async def scan():
            return {'hosts': [{'id': 'id', 'name': 'host', 'host': '192.168.1.2', 'port': 8000}], 'connection': None}
        monkeypatch.setattr(app.state.lan_connections, 'scan', scan)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/v1/execution-hosts')).status_code == 401
            result = await client.get('/v1/execution-hosts', headers={'Authorization': 'Bearer local-secret'})
            assert result.status_code == 200
            assert result.json()['hosts'][0]['id'] == 'id'
    asyncio.run(run())


def test_connect_requires_discovery_and_does_not_return_credentials():
    async def run():
        app = create_app(lambda: None, token='local-secret')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer local-secret'}) as client:
            response = await client.post('/v1/execution-hosts/connect', json={'id': 'unknown'})
            assert response.status_code == 409
            assert 'refresh' in response.text.lower()
    asyncio.run(run())
