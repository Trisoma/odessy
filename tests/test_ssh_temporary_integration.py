"""Native local monitoring pipeline with a clearly simulated SSH computer.

The supervisor, Go monitor, OS config/key lookup and password helper are real.
Only remote SSH authentication/commands are replaced by the existing fakessh child.
No network host, trust-file change, model or installed application is required.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess

import httpx
import pytest

from odessy.application.api import create_app
from odessy.monitoring.settings import MonitoringSettings
from odessy.monitoring.supervisor import MonitoringSupervisor


@pytest.mark.parametrize('password', ['', 'fixture-password'])
def test_temporary_connect_real_monitor_sample_disconnect_restart(tmp_path, monkeypatch, password):
    binary = os.environ.get('ODESSY_TEST_MONITOR_BIN', '')
    fixture = os.environ.get('ODESSY_TEST_SSH_BIN', '')
    ssh, keygen = shutil.which('ssh'), shutil.which('ssh-keygen')
    if not binary or not fixture:
        pytest.skip('Set ODESSY_TEST_MONITOR_BIN and ODESSY_TEST_SSH_BIN to source-built fixtures')
    assert Path(binary).is_absolute() and Path(binary).is_file()
    assert Path(fixture).is_absolute() and Path(fixture).is_file()
    if not ssh or not keygen:
        pytest.skip('OpenSSH tools unavailable')
    key = tmp_path / 'fixture-key'
    subprocess.run([keygen, '-q', '-t', 'ed25519', '-N', '', '-f', str(key)],
                   check=True, capture_output=True, timeout=10)
    public = key.with_suffix('.pub').read_text().strip()
    known = tmp_path / 'known hosts'
    known.write_text('[192.0.2.10]:2222 ' + public + '\n')
    trusted = known.read_bytes()
    config = tmp_path / 'ssh-config'
    config.write_text('Host *\n Port 22\n UserKnownHostsFile "' + known.as_posix() + '"\n')
    user_config = tmp_path / 'odessy.env'
    user_config.write_bytes(b'# unrelated user configuration\r\nVOICE=on\r\n')
    original = user_config.read_bytes()
    monkeypatch.setenv('ODESSY_CONFIG_FILE', str(user_config))
    monkeypatch.setenv('ODESSY_TEST_REAL_SSH', ssh)
    monkeypatch.setenv('ODESSY_TEST_SSH_PASSWORD', password)
    # Disable only unauthenticated network key discovery. Trust still comes from
    # real ssh-keygen reading the isolated known-host file, never this fixture.
    monkeypatch.setattr('odessy.ssh.identity.discovered_keys', lambda *a, **kw: ())

    async def run():
        settings = MonitoringSettings(binary=binary, ssh_binary=fixture,
            ssh_config=str(config), state_dir=str(tmp_path / 'monitor-state'))
        monitor = MonitoringSupervisor(settings)
        app = create_app(lambda: None, token='fixture-token', monitoring=monitor)
        headers = {'Authorization': 'Bearer fixture-token'}
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                body = {'target': 'tester@192.0.2.10', 'port': 2222, 'password': password}
                if password:
                    denied = await client.post('/v1/ssh/connect', headers=headers,
                        json={**body, 'password': 'incorrect-fixture-password'})
                    assert denied.status_code == 200, denied.text
                    assert denied.json()['connected'] is False
                    assert monitor.process is None
                response = await client.post('/v1/ssh/connect', headers=headers, json=body)
                assert response.status_code == 200, response.text
                result = response.json()
                assert result['connected'] and result['target']['temporary']
                assert result['target']['port'] == 2222
                assert result['target']['display_name'] == 'ubuntu-fixture'
                assert monitor.process is not None
                process = monitor.process
                lease = monitor.ssh_password_lease
                host_id = result['target']['host_id']
                for _ in range(80):
                    status = await client.get('/v1/ssh/computer', headers=headers)
                    metrics = await client.get('/v1/monitoring/hosts/' + host_id + '/metrics', headers=headers)
                    if metrics.status_code == 200 and (metrics.json().get('sample') or {}).get('cpu'):
                        break
                    await asyncio.sleep(.1)
                assert status.status_code == 200, status.text
                computer = status.json()['computer']
                assert computer['capabilities']['monitoring'] is True
                assert computer['capabilities']['execution'] is False
                assert computer['capabilities']['workspace'] is False
                assert computer['status']['observed_at']
                assert computer['status']['connectivity'] == 'online'
                assert computer['display_name'] == 'ubuntu-fixture'
                hosts = await client.get('/v1/monitoring/hosts', headers=headers)
                assert hosts.status_code == 200
                connected = next(h for h in hosts.json()['hosts'] if h['host_id'] == host_id)
                assert connected['display_name'] == 'ubuntu-fixture'
                assert connected['connection']['method'] == 'ssh'
                assert connected['connection']['alias'] == 'tester@192.0.2.10'
                assert metrics.status_code == 200, metrics.text
                assert metrics.json()['sample']['cpu']['load_1'] == .08, metrics.text
                if password:
                    assert password not in json.dumps([result, computer, metrics.json()])
                assert user_config.read_bytes() == original
                removed = await client.delete('/v1/ssh/target', headers=headers)
                assert removed.status_code == 200, removed.text
                assert monitor.process is None
                assert process.returncode is not None
                if lease:
                    assert not lease.active
                assert (await client.get('/v1/ssh/target', headers=headers)).json()['target'] is None
        finally:
            app.state.ssh_connections.close()
            await monitor.close()
        restarted = MonitoringSupervisor(settings)
        try:
            app = create_app(lambda: None, token='fixture-token', monitoring=restarted)
            assert app.state.ssh_connections.target()['target'] is None
            assert restarted.process is None
        finally:
            await restarted.close()
    asyncio.run(run())
    assert user_config.read_bytes() == original
    assert known.read_bytes() == trusted
