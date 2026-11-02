import asyncio
import httpx
from odessy.application.api import create_app
from odessy.config.schema import SETTINGS


def test_runtime_inventory_requires_auth_and_never_exposes_values(monkeypatch):
    async def run():
        monkeypatch.setenv('ODESSY_API_TOKEN', 'fixture-secret-do-not-return')
        monkeypatch.setenv('ODESSY_AUDIO_PLAYER', 'fixture-command-do-not-return')
        app = create_app(lambda: None, token='fixture-token')
        other = create_app(lambda: None, token='fixture-token')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/v1/runtime-settings')).status_code == 401
            headers = {'Authorization': 'Bearer fixture-token'}
            response = await client.get('/v1/runtime-settings', headers=headers)
            assert response.status_code == 200
            data = response.json()
            assert data['schema_version'] == 1
            assert data['instance_id'] != other.state.runtime_instance_id
            assert len(data['settings']) == len(SETTINGS)
            assert 'fixture-secret-do-not-return' not in response.text
            assert 'fixture-command-do-not-return' not in response.text
            secret = next(item for item in data['settings'] if item['name'] == 'ODESSY_API_TOKEN')
            assert secret['sensitive'] and secret['configured'] and not secret['editable']
            assert all('value' not in item and not item['editable'] for item in data['settings'])
            assert (await client.put('/v1/runtime-settings', headers=headers, json={})).status_code == 405
    asyncio.run(run())
