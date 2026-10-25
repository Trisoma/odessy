import asyncio
import httpx
import pytest
from odessy.monitoring.supervisor import MonitoringSupervisor, MonitorHTTPError, MonitorUnavailable
from odessy.monitoring.settings import MonitoringSettings


def test_bounded_internal_errors_keep_public_status():
    async def run():
        monitor = MonitoringSupervisor(MonitoringSettings())
        monitor.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
            httpx.Response(404, json={'schema_version': 1, 'error': {'code': 'HOST_NOT_FOUND', 'message': 'HOST_NOT_FOUND', 'retryable': False}})), base_url='http://127.0.0.1')
        with pytest.raises(MonitorHTTPError) as error:
            await monitor.request('GET', '/internal/v1/hosts/host_a')
        assert error.value.status == 404
        await monitor.close()
    asyncio.run(run())


def test_sse_limit_is_enforced_before_unterminated_line_is_accumulated():
    async def run():
        consumed = []
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for i in range(100):
                    consumed.append(i)
                    yield b'x' * 65536
        monitor = MonitoringSupervisor(MonitoringSettings())
        monitor.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=Stream())),base_url='http://127.0.0.1')
        with pytest.raises(MonitorUnavailable):
            async for _ in monitor.events(): pass
        assert len(consumed) <= 33
        await monitor.close()
    asyncio.run(run())
