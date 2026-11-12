import asyncio
import json
import sys


def test_start_request_and_events_without_undeclared_httpx(tmp_path, monkeypatch):
    """Use the shipping HTTP client with no dev-only httpx fallback."""
    from odessy.monitoring.settings import MonitoringSettings
    from odessy.monitoring.supervisor import MonitoringSupervisor
    monkeypatch.setitem(sys.modules, 'httpx', None)
    binary = tmp_path / 'monitor'
    binary.write_bytes(b'fixture, never executed')
    requests = []

    async def run():
        async def serve(reader, writer):
            headers = await reader.readuntil(b'\r\n\r\n')
            requests.append(headers)
            body = (b'id: 1\ndata: {"schema_version":1}\n\n'
                    if b'/events ' in headers else b'{"schema_version":1,"hosts":[]}')
            writer.write(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: '
                         + str(len(body)).encode() + b'\r\n\r\n' + body)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(serve, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        class Input:
            def write(self, data): self.config = json.loads(data)
            async def drain(self): pass
            def close(self): pass
        class Process:
            stdin = Input()
            stdout = asyncio.StreamReader()
            stderr = asyncio.StreamReader()
            returncode = 0
            async def wait(self): return 0
        process = Process()
        process.stdout.feed_data(json.dumps({'schema_version': 1, 'op': 'ready',
            'base_url': f'http://127.0.0.1:{port}'}).encode() + b'\n')
        process.stderr.feed_eof()
        async def spawn(*args, **kwargs): return process
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
        monitor = MonitoringSupervisor(MonitoringSettings(enabled=True,
            binary=str(binary), link_binary=str(binary)))
        try:
            await monitor.start()
            assert monitor.client.__class__.__module__.startswith('httpx2')
            assert await monitor.request('GET', '/internal/v1/hosts') == {'schema_version': 1, 'hosts': []}
            assert [frame async for frame in monitor.events('previous')] == [b'id: 1\ndata: {"schema_version":1}\n\n']
            assert all(('Authorization: Bearer ' + monitor.token).encode() in raw for raw in requests)
            assert b'Last-Event-ID: previous' in requests[1]
        finally:
            await monitor.close()
            server.close()
            await server.wait_closed()
        assert monitor.client is None and monitor.process is None
    asyncio.run(run())
