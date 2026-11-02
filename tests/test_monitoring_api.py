import asyncio
import httpx
from odessy.application.api import create_app


class Monitor:
    async def request(self, method, path, query=None):
        if path == '/internal/v1/hosts':
            return {'schema_version': 1, 'hosts': [], 'event_cursor': 'a:1', 'next_cursor': None}
        return {'schema_version': 1, 'status': 'healthy'}
    async def events(self, cursor=None):
        yield b'id: a:1\nevent: monitor.health\ndata: {"schema_version":1,"status":"healthy"}\n\n'


def test_monitoring_auth_disabled_state_and_no_arbitrary_proxy():
    async def run():
        for monitor, expected in [(None, 503), (Monitor(), 200)]:
            app = create_app(lambda: None, token='secret', monitoring=monitor)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                assert (await client.get('/v1/monitoring')).status_code == 401
                headers = {'Authorization': 'Bearer secret'}
                response = await client.get('/v1/monitoring', headers=headers)
                assert response.status_code == expected
                if monitor:
                    assert (await client.get('/v1/monitoring/hosts', headers=headers)).status_code == 200
                    assert (await client.get('/v1/monitoring?url=http://evil', headers=headers)).status_code == 400
                    stream = await client.get('/v1/monitoring/events', headers=headers)
                    assert 'monitor.health' in stream.text
    asyncio.run(run())


def test_invalid_optional_monitor_configuration_does_not_disable_sessions(monkeypatch):
    monkeypatch.setenv('ODESSY_MONITOR_PRESET', 'invalid')
    app = create_app(lambda: None, token='secret')
    assert app.state.auth_token == 'secret'
