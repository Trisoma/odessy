"""Owned Go process and bounded private HTTP transport. No automatic replay."""
import asyncio
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
from urllib.parse import urlsplit

MAX_JSON = 2 * 1024 * 1024
PATH = re.compile(r'/internal/v1/(?:monitoring|hosts(?:/host_[a-zA-Z0-9_]{1,128}(?:/(?:metrics|history))?)?|events|runtime-observations)')


class MonitorUnavailable(RuntimeError):
    pass


class MonitorHTTPError(MonitorUnavailable):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


class MonitoringSupervisor:
    def __init__(self, settings):
        self.settings = settings
        self.process = None
        self.client = None
        self.base_url = None
        self.token = secrets.token_urlsafe(32)
        self._stderr = None
        self.runtime_sink = None

    async def start(self):
        if not self.settings.enabled or self.process is not None:
            return
        binary = Path(self.settings.binary)
        link = Path(self.settings.link_binary)
        if not binary.is_absolute() or not binary.is_file() or not link.is_absolute() or not link.is_file():
            raise MonitorUnavailable('Configure absolute monitoring and link binary paths')
        import httpx
        options = {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        self.process = await asyncio.create_subprocess_exec(str(binary), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=65536, **options)
        self._stderr = asyncio.create_task(self._drain_stderr())
        try:
            config = {'schema_version': 1, 'op': 'configure', 'token': self.token,
                      'state_dir': self.settings.state_dir, 'link_binary': str(link), 'settings': self.settings.wire()}
            self.process.stdin.write(json.dumps(config).encode() + b'\n')
            await self.process.stdin.drain()
            raw = await asyncio.wait_for(self.process.stdout.readline(), timeout=10)
            ready = json.loads(raw)
            parsed = urlsplit(ready['base_url'])
            if ready.get('schema_version') != 1 or ready.get('op') != 'ready' or parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
                raise MonitorUnavailable('Invalid monitor readiness')
            self.base_url = ready['base_url'].rstrip('/')
            self.client = httpx.AsyncClient(base_url=self.base_url, headers={'Authorization': 'Bearer ' + self.token},
                                            timeout=5, follow_redirects=False, trust_env=False)
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception:
            await self.close()
            raise MonitorUnavailable('Monitoring startup failed') from None

    async def _drain_stderr(self):
        # Discard rather than retain untrusted child diagnostics/credentials.
        while await self.process.stderr.read(4096):
            pass

    async def local_identity(self):
        """Read the Rust-owned identity; never inspect its SQLite/private key files."""
        proc = await asyncio.create_subprocess_exec(self.settings.link_binary, 'telemetry', 'worker',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=256 * 1024,
            **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
        try:
            proc.stdin.write(b'{"schema_version":1,"id":"identity","op":"inventory"}\n')
            await proc.stdin.drain()
            raw = await asyncio.wait_for(proc.stdout.readline(), 3)
            value = json.loads(raw)
            if value.get('ok') is not True:
                raise MonitorUnavailable('Local identity unavailable')
            return next(h for h in value['result']['hosts'] if h['trust_state'] == 'local')
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()

    async def attach(self, controller):
        if not self.settings.include_local or not self.client:
            return
        try:
            if self.runtime_sink is None:
                from .runtime import RuntimeObservationSink
                local = await self.local_identity()
                async def send(value):
                    current = await self.local_identity()
                    value['host_id'], value['generation'] = current['host_id'], current['generation']
                    value['services'] = {'coordinator': {'status': 'healthy', 'observed_at': value['observed_at'], 'source': 'runtime'}}
                    await self.request('POST', '/internal/v1/runtime-observations', body=value)
                self.runtime_sink = RuntimeObservationSink(send, host_id=local['host_id'], generation=local['generation'])
            controller.monitoring_sink = self.runtime_sink
        except Exception:
            pass  # Observability must never prevent a user turn.

    async def request(self, method, path, query=None, body=None):
        if not self.client or not PATH.fullmatch(path) or method not in ('GET', 'POST'):
            raise MonitorUnavailable('Monitoring unavailable')
        if method == 'POST' and path != '/internal/v1/runtime-observations':
            raise MonitorUnavailable('Unsupported monitor operation')
        try:
            async with self.client.stream(method, path, params=query, json=body) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > MAX_JSON:
                        raise MonitorUnavailable('Monitoring response exceeds limit')
                    data.extend(chunk)
                value = json.loads(data)
                if not isinstance(value, dict) or value.get('schema_version') != 1:
                    raise MonitorUnavailable('Invalid monitor response')
                if response.status_code >= 300:
                    code = value.get('error', {}).get('code')
                    if response.status_code in (400, 403, 404, 409, 429, 503) and code in (
                        'HOST_NOT_FOUND', 'HOST_ACCESS_DENIED', 'INVALID_REQUEST', 'INVALID_RANGE',
                        'RESOURCE_LIMIT', 'MONITOR_UNAVAILABLE'):
                        raise MonitorHTTPError(response.status_code, code)
                    raise MonitorUnavailable('Monitoring request failed')
                return value
        except asyncio.CancelledError:
            raise
        except MonitorHTTPError:
            raise
        except Exception:
            raise MonitorUnavailable('Monitoring unavailable') from None

    async def events(self, cursor=None):
        if not self.client:
            raise MonitorUnavailable('Monitoring unavailable')
        headers = {'Last-Event-ID': cursor} if cursor else {}
        try:
            async with self.client.stream('GET', '/internal/v1/events', headers=headers, timeout=None) as response:
                if response.status_code != 200:
                    raise MonitorUnavailable('Monitoring stream unavailable')
                buffer = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(buffer) + len(chunk) > MAX_JSON:
                        raise MonitorUnavailable('Monitoring event exceeds limit')
                    buffer.extend(chunk)
                    while b'\n\n' in buffer or b'\r\n\r\n' in buffer:
                        split = b'\r\n\r\n' if b'\r\n\r\n' in buffer else b'\n\n'
                        frame, _, rest = buffer.partition(split)
                        buffer = bytearray(rest)
                        yield bytes(frame) + split
                if buffer.strip():
                    raise MonitorUnavailable('Truncated monitoring event')
        except asyncio.CancelledError:
            raise
        except Exception:
            raise MonitorUnavailable('Monitoring stream unavailable') from None

    async def close(self):
        if self.runtime_sink:
            await self.runtime_sink.close()
            self.runtime_sink = None
        if self.client:
            await self.client.aclose()
            self.client = None
        proc = self.process
        if proc:
            if proc.stdin:
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                if os.name == 'nt':
                    killer = await asyncio.create_subprocess_exec('taskkill.exe', '/PID', str(proc.pid), '/T', '/F',
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                    await asyncio.wait_for(killer.wait(), 5)
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
                await asyncio.wait_for(proc.wait(), timeout=3)
            self.process = None
        if self._stderr:
            self._stderr.cancel()
            await asyncio.gather(self._stderr, return_exceptions=True)
