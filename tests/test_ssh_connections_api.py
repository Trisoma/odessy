"""The Connect computer application actions: aliases, review, test, save, remove."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from odessy.application.api import create_app
from odessy.application.ssh_connections import SshConnectionError, SshConnections
from odessy.monitoring.settings import MonitoringSettings


class FakeMonitor:
    def __init__(self):
        self.settings = MonitoringSettings(
            enabled=True, host_ids=(), binary='monitor', link_binary='',
            ssh_alias='', ssh_host_id='', ssh_monitoring=False, ssh_binary='fixture-ssh')
        self.reconfigured = None

    async def reconfigure(self, settings):
        self.settings = settings
        self.reconfigured = settings


def identity():
    return {
        'alias': 'jetson', 'hostname': '192.0.2.10', 'user': 'tester',
        'port': 22, 'endpoint': '192.0.2.10', 'known': True,
        'known_fingerprints': ['SHA256:known'],
        'discovered_fingerprints': ['SHA256:known'],
        'changed': False, 'discovery': 'ssh-keyscan (unauthenticated)',
        'known_error': '', 'discover_error': '',
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    config = tmp_path / 'odessy.env'
    monkeypatch.setenv('ODESSY_CONFIG_FILE', str(config))
    monkeypatch.setattr('odessy.application.ssh_connections.list_aliases',
                        lambda _path: ('jetson', 'other'))
    monkeypatch.setattr('odessy.application.ssh_connections.inspect_alias',
                        lambda alias, **_kwargs: {**identity(), 'alias': alias})
    monkeypatch.setattr('odessy.application.ssh_connections.resolve_host',
                        lambda *_args, **_kwargs: object())
    monkeypatch.setattr('odessy.application.ssh_connections.probe_connection',
                        lambda *_args, **_kwargs: {
                            'ok': True, 'code': 'SSH_OK', 'message': '', 'elapsed_ms': 12.0})
    return config


def test_target_actions_save_and_remove(tmp_path, env):
    monitor = FakeMonitor()

    async def run():
        manager = SshConnections(monitor)
        aliases = await manager.aliases()
        assert [row['alias'] for row in aliases['aliases']] == ['jetson', 'other']
        reviewed = await manager.inspect('jetson')
        assert reviewed['identity']['known'] is True
        tested = await manager.test('jetson')
        assert tested['test']['ok'] is True
        saved = await manager.save('jetson', 'Jetson')
        assert saved['target']['alias'] == 'jetson'
        assert monitor.settings.ssh_host_id in monitor.settings.host_ids
        assert env.exists() and 'ODESSY_SSH_ALIAS=jetson' in env.read_text()
        removed = await manager.remove()
        assert removed['removed'] is True
        assert 'revoke' in removed['message']
        assert 'ODESSY_SSH_ALIAS' not in env.read_text()

    asyncio.run(run())


def test_unknown_and_changed_hosts_are_refused_before_save(monkeypatch, env):
    monitor = FakeMonitor()
    monkeypatch.setattr('odessy.application.ssh_connections.inspect_alias',
                        lambda alias, **_kwargs: {
                            **identity(), 'alias': alias, 'known': False,
                            'known_fingerprints': [], 'changed': False,
                            'discovered_fingerprints': ['SHA256:new']})

    async def run():
        manager = SshConnections(monitor)
        with pytest.raises(SshConnectionError) as unknown:
            await manager.test('jetson')
        assert unknown.value.status == 409
        with pytest.raises(SshConnectionError):
            await manager.save('jetson')

    asyncio.run(run())

    monkeypatch.setattr('odessy.application.ssh_connections.inspect_alias',
                        lambda alias, **_kwargs: {
                            **identity(), 'alias': alias, 'changed': True,
                            'discovered_fingerprints': ['SHA256:new']})
    async def changed():
        with pytest.raises(SshConnectionError) as error:
            await SshConnections(monitor).test('jetson')
        assert error.value.status == 409
    asyncio.run(changed())


def test_api_requires_auth_and_exposes_target(env):
    monitor = FakeMonitor()

    async def run():
        app = create_app(lambda: None, token='secret', monitoring=monitor)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            assert (await client.get('/v1/ssh/aliases')).status_code == 401
            headers = {'Authorization': 'Bearer secret'}
            aliases = await client.get('/v1/ssh/aliases', headers=headers)
            assert aliases.status_code == 200
            assert aliases.json()['aliases'][0]['alias'] == 'jetson'
            target = await client.post('/v1/ssh/target', headers=headers,
                                       json={'alias': 'jetson'})
            assert target.status_code == 200, target.text
            assert target.json()['target']['alias'] == 'jetson'
            current = await client.get('/v1/ssh/target', headers=headers)
            assert current.json()['target']['host_id'].startswith('host_')
            removed = await client.delete('/v1/ssh/target', headers=headers)
            assert removed.status_code == 200

    asyncio.run(run())

def test_failed_login_is_not_saved(monkeypatch, env):
    monitor = FakeMonitor()
    monkeypatch.setattr('odessy.application.ssh_connections.probe_connection',
                        lambda *_args, **_kwargs: {
                            'ok': False, 'code': 'SSH_AUTH_FAILED',
                            'message': 'Permission denied', 'elapsed_ms': 3.0})

    async def run():
        with pytest.raises(SshConnectionError) as error:
            await SshConnections(monitor).save('jetson')
        assert error.value.status == 409
        assert monitor.reconfigured is None
        assert not env.exists()

    asyncio.run(run())

def test_save_rejects_newline_display_name_before_runtime_or_file_change(env):
    monitor = FakeMonitor()

    async def run():
        with pytest.raises(SshConnectionError) as error:
            await SshConnections(monitor).save("jetson", "ok\nODESSY_SSH_COMPUTER=off")
        assert error.value.status == 400
        assert monitor.reconfigured is None
        assert not env.exists()

    asyncio.run(run())


def test_save_restores_previous_config_when_reconfigure_fails(env):
    class FailingOnce(FakeMonitor):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def reconfigure(self, settings):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("startup failed")
            await super().reconfigure(settings)

    monitor = FailingOnce()
    monitor.settings = MonitoringSettings(
        enabled=True, host_ids=(), binary="monitor", link_binary="",
        ssh_alias="old", ssh_host_id="host_" + "c" * 64,
        ssh_monitoring=False)
    env.write_text("ODESSY_SSH_ALIAS=old\nODESSY_SSH_COMPUTER=on\n", encoding="utf-8")

    async def run():
        with pytest.raises(SshConnectionError) as error:
            await SshConnections(monitor).save("jetson", "Jetson")
        assert error.value.status == 503
        assert "previous target" in str(error.value).lower()
        assert env.read_text(encoding="utf-8") == "ODESSY_SSH_ALIAS=old\nODESSY_SSH_COMPUTER=on\n"
        assert monitor.settings.ssh_alias == "old"

    asyncio.run(run())


def test_save_does_not_reconfigure_when_config_write_fails(env, monkeypatch):
    monitor = FakeMonitor()
    monkeypatch.setattr("odessy.application.ssh_connections.update_env_values",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))

    async def run():
        with pytest.raises(SshConnectionError) as error:
            await SshConnections(monitor).save("jetson")
        assert error.value.status == 503
        assert monitor.reconfigured is None
        assert not env.exists()

    asyncio.run(run())

def test_concurrent_target_saves_are_serialized(env):
    class SerialMonitor(FakeMonitor):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.max_active = 0
            self.calls = 0

        async def reconfigure(self, settings):
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.02)
            await super().reconfigure(settings)
            self.active -= 1

    monitor = SerialMonitor()

    async def run():
        manager = SshConnections(monitor)
        await asyncio.gather(manager.save("jetson"), manager.save("other"))
        assert monitor.calls == 2
        assert monitor.max_active == 1
        assert monitor.settings.ssh_alias == "other"

    asyncio.run(run())

def test_remove_restores_target_when_reconfigure_fails(env):
    class FailingOnce(FakeMonitor):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def reconfigure(self, settings):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("stop failed")
            await super().reconfigure(settings)

    monitor = FailingOnce()
    monitor.settings = MonitoringSettings(
        enabled=True, host_ids=("host_" + "c" * 64,), binary="monitor",
        link_binary="", ssh_alias="old", ssh_host_id="host_" + "c" * 64,
        ssh_monitoring=True)
    env.write_text("ODESSY_SSH_ALIAS=old\nODESSY_SSH_COMPUTER=on\n", encoding="utf-8")

    async def run():
        with pytest.raises(SshConnectionError) as error:
            await SshConnections(monitor).remove()
        assert error.value.status == 503
        assert env.read_text(encoding="utf-8") == "ODESSY_SSH_ALIAS=old\nODESSY_SSH_COMPUTER=on\n"
        assert monitor.settings.ssh_alias == "old"

    asyncio.run(run())


@pytest.mark.parametrize('paired', [False, True])
def test_replacing_target_then_removing_preserves_only_paired_hosts(env, paired):
    from dataclasses import replace
    monitor = FakeMonitor()
    link = env.parent / 'link'
    link.write_bytes(b'fixture')
    monitor.settings = replace(monitor.settings, host_ids=('paired-host',) if paired else (),
                               link_binary=str(link) if paired else '')

    async def run():
        manager = SshConnections(monitor)
        await manager.save('jetson')
        await manager.save('other')
        assert manager.host_id('jetson') not in monitor.settings.host_ids
        assert monitor.settings.host_ids == (('paired-host',) if paired else ()) + (manager.host_id('other'),)
        await manager.remove()
        assert monitor.settings.host_ids == (('paired-host',) if paired else ())
        assert monitor.settings.enabled is paired
        assert monitor.settings.ssh_alias == ''
    asyncio.run(run())


def test_cancelled_mutation_keeps_lock_until_runtime_settles(env):
    async def run():
        monitor = FakeMonitor()
        manager = SshConnections(monitor)
        entered, release = asyncio.Event(), asyncio.Event()
        async def reconfigure(settings):
            entered.set()
            await release.wait()
            monitor.settings = settings
        monitor.reconfigure = reconfigure
        task = asyncio.create_task(manager.save('jetson'))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert manager._lock.locked()
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not manager._lock.locked()
        assert monitor.settings.ssh_alias == 'jetson'
    asyncio.run(run())
