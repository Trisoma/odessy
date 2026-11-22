import asyncio
import httpx2 as httpx
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


def test_reconfigure_preserves_existing_runtime_forwarder():
    import asyncio
    from odessy.monitoring.settings import MonitoringSettings
    from odessy.monitoring.supervisor import MonitoringSupervisor
    from odessy.monitoring.runtime import RuntimeObservationSink
    async def run():
        supervisor = MonitoringSupervisor(MonitoringSettings())
        async def send(value):
            pass
        sink = RuntimeObservationSink(send, host_id='fixture', generation='g')
        supervisor.runtime_sink = sink
        await supervisor.reconfigure(MonitoringSettings())
        assert supervisor.runtime_sink is sink
        assert not sink.closed
        await supervisor.close()
        assert sink.closed
    asyncio.run(run())


def test_supervisor_bootstrap_pins_direct_address_and_explicit_port(tmp_path):
    import asyncio
    import shutil
    import subprocess
    from odessy.monitoring.settings import MonitoringSettings
    from odessy.monitoring.supervisor import MonitoringSupervisor
    ssh, keygen = shutil.which('ssh'), shutil.which('ssh-keygen')
    if not ssh or not keygen:
        pytest.skip('OpenSSH unavailable')
    key = tmp_path / 'fixture-key'
    subprocess.run([keygen, '-q', '-t', 'ed25519', '-N', '', '-f', str(key)],
                   check=True, capture_output=True, timeout=10)
    public = key.with_suffix('.pub').read_text().strip()
    known = tmp_path / 'known hosts'
    known.write_text('[192.0.2.10]:2222 ' + public + '\n')
    before = known.read_bytes()
    config = tmp_path / 'ssh-config'
    config.write_text('Host *\n Port 22\n UserKnownHostsFile "' + known.as_posix() + '"\n')
    async def run():
        monitor = MonitoringSupervisor(MonitoringSettings(
            ssh_alias='tester@192.0.2.10', ssh_port=2222, ssh_temporary=True,
            ssh_binary=ssh, ssh_config=str(config), ssh_monitoring=True,
            ssh_host_id='host_' + 'a' * 64))
        try:
            bootstrap = await monitor._ssh_config()
            assert bootstrap['hostname'] == '192.0.2.10'
            assert bootstrap['user'] == 'tester'
            assert bootstrap['port'] == 2222
            assert bootstrap['monitoring'] is True
            assert bootstrap['host_key'] == ' '.join(public.split()[:2])
            assert bootstrap['fingerprint_sha256'].startswith('SHA256:')
            assert 'password_token' not in bootstrap
        finally:
            await monitor.close()
    asyncio.run(run())
    assert known.read_bytes() == before
