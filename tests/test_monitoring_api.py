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


def test_history_repeated_series_survive_public_api_and_supervisor():
    """Exercise the real public route and HTTPX forwarding, with a fake Go endpoint."""
    from odessy.monitoring.settings import MonitoringSettings
    from odessy.monitoring.supervisor import MonitoringSupervisor

    async def run():
        seen = []

        def upstream(request):
            assert request.url.path == '/internal/v1/hosts/host_a/history'
            seen.append(request.url.params.get_list('series'))
            return httpx.Response(200, json={'schema_version': 1, 'series': []})

        monitor = MonitoringSupervisor(MonitoringSettings())
        monitor.client = httpx.AsyncClient(
            base_url='http://monitor.test', transport=httpx.MockTransport(upstream))
        app = create_app(lambda: None, token='fixture-token', monitoring=monitor)
        headers = {'Authorization': 'Bearer fixture-token'}
        names = ['cpu.utilization_percent', 'memory.used_bytes',
                 'memory.total_bytes', 'memory.utilization_percent',
                 'gpu.utilization_percent', 'gpu.memory_used_bytes']
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url='http://app.test') as client:
                path = '/v1/monitoring/hosts/host_a/history'
                result = await client.get(path, headers=headers,
                                          params=[('series', name) for name in names])
                assert result.status_code == 200
                assert seen[-1] == names
                single = await client.get(path, headers=headers,
                                          params={'series': names[0]})
                assert single.status_code == 200
                assert seen[-1] == names[:1]
                denied = await client.get(path, headers=headers,
                                          params={'url': 'http://evil'})
                assert denied.status_code == 400
                assert len(seen) == 2
        finally:
            await monitor.client.aclose()
    asyncio.run(run())
