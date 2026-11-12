"""Production supervisor must bootstrap link and SSH into one Go process."""

from __future__ import annotations

import asyncio
import json

import pytest

from odessy.monitoring.settings import MonitoringSettings
from odessy.monitoring.supervisor import MonitoringSupervisor, MonitorUnavailable


class FakeStdin:
    def __init__(self):
        self.data = b''
        self.closed = False

    def write(self, value):
        self.data += value

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class FakeStream:
    def __init__(self, data=b''):
        self.data = data

    async def read(self, _size):
        return self.data

    async def readline(self):
        return self.data


class FakeProcess:
    def __init__(self):
        self.stdin = FakeStdin()
        self.stdout = FakeStream(
            json.dumps({'schema_version': 1, 'op': 'ready',
                        'base_url': 'http://127.0.0.1:12345'}).encode() + b'\n')
        self.stderr = FakeStream()
        self.pid = 1
        self.returncode = None

    async def wait(self):
        self.returncode = 0
        return 0


def settings_with_files(tmp_path, **overrides):
    binary = tmp_path / 'odessy-monitor.exe'
    link = tmp_path / 'odessy-link.exe'
    binary.write_bytes(b'x')
    link.write_bytes(b'x')
    values = dict(enabled=True, binary=str(binary), link_binary=str(link),
                  state_dir=str(tmp_path), ssh_alias='jetson',
                  ssh_host_id='host_' + 'a' * 64,
                  host_ids=('host_' + 'a' * 64,), ssh_monitoring=True)
    values.update(overrides)
    return MonitoringSettings(**values)


def test_start_bootstraps_both_link_and_ssh(monkeypatch, tmp_path):
    processes = []

    async def fake_exec(*_args, **_kwargs):
        process = FakeProcess()
        processes.append(process)
        return process

    async def fake_ssh(_self):
        return {'binary': 'ssh', 'alias': 'jetson', 'host_id': 'host_' + 'a' * 64,
                'fingerprint_sha256': 'SHA256:test', 'generation': 'ssh:test',
                'monitoring': True}

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_exec)
    monkeypatch.setattr(MonitoringSupervisor, '_ssh_config', fake_ssh)

    async def run():
        monitor = MonitoringSupervisor(settings_with_files(tmp_path))
        await monitor.start()
        frame = json.loads(processes[0].stdin.data.decode())
        assert frame['link_binary'].endswith('odessy-link.exe')
        assert frame['ssh']['alias'] == 'jetson'
        assert frame['settings']['monitored_host_ids'] == ['host_' + 'a' * 64]
        await monitor.close()
        assert processes[0].stdin.closed

    asyncio.run(run())


def test_optional_ssh_failure_keeps_link_monitoring(monkeypatch, tmp_path):
    processes = []

    async def fake_exec(*_args, **_kwargs):
        process = FakeProcess()
        processes.append(process)
        return process

    async def failing_ssh(_self):
        raise MonitorUnavailable('host key changed')

    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_exec)
    monkeypatch.setattr(MonitoringSupervisor, '_ssh_config', failing_ssh)

    async def run():
        monitor = MonitoringSupervisor(settings_with_files(tmp_path))
        await monitor.start()
        frame = json.loads(processes[0].stdin.data.decode())
        assert frame['link_binary'].endswith('odessy-link.exe')
        assert 'ssh' not in frame
        assert 'host key changed' in monitor.ssh_error
        await monitor.close()

    asyncio.run(run())
