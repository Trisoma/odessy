"""Fresh-default SSH bootstrap: aliases work before monitoring is enabled."""

from __future__ import annotations

import asyncio

import httpx

from odessy.application.api import create_app
from odessy.monitoring.settings import MonitoringSettings


class FakeSupervisor:
    instances = []

    def __init__(self, settings):
        self.settings = settings
        self.started = False
        self.reconfigured = None
        self.requested = []
        FakeSupervisor.instances.append(self)

    async def start(self):
        self.started = True

    async def close(self):
        self.started = False

    async def reconfigure(self, settings):
        self.settings = settings
        self.reconfigured = settings
        self.started = True

    async def request(self, method, path, query=None):
        self.requested.append((method, path))
        return {
            'schema_version': 1,
            'host': {
                'host_id': self.settings.ssh_host_id,
                'display_name': 'Jetson',
                'fingerprint_sha256': 'SHA256:known',
                'trust': 'ssh',
                'connectivity': 'online',
                'monitoring_access': 'allowed',
                'generation': 'ssh:SHA256:known',
                'sample': {'observed_at': '2026-11-11T12:00:00Z'},
                'stale_groups': [],
            },
        }


def test_fresh_default_aliases_then_explicit_save_then_status(monkeypatch):
    monkeypatch.setenv("ODESSY_SSH_BIN", "fixture-ssh")
    for name in ('ODESSY_MONITORING', 'ODESSY_MONITOR_LOCAL', 'ODESSY_SSH_COMPUTER',
                 'ODESSY_SSH_MONITORING', 'ODESSY_SSH_ALIAS', 'ODESSY_SSH_HOST_ID',
                 'ODESSY_SSH_CONFIG', 'ODESSY_MONITOR_HOSTS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('odessy.monitoring.supervisor.MonitoringSupervisor',
                        FakeSupervisor)
    monkeypatch.setattr('odessy.application.ssh_connections.list_aliases',
                        lambda _path: ('jetson',))
    monkeypatch.setattr('odessy.application.ssh_connections.inspect_alias',
                        lambda alias, **_kwargs: {
                            'alias': alias, 'hostname': '192.0.2.10', 'user': 'tester',
                            'port': 22, 'endpoint': '192.0.2.10', 'known': True,
                            'known_fingerprints': ['SHA256:known'],
                            'discovered_fingerprints': ['SHA256:known'],
                            'changed': False, 'discovery': '',
                        })
    monkeypatch.setattr('odessy.application.ssh_connections.resolve_host',
                        lambda *_args, **_kwargs: object())
    monkeypatch.setattr('odessy.application.ssh_connections.probe_connection',
                        lambda *_args, **_kwargs: {
                            'ok': True, 'code': 'SSH_OK', 'message': '', 'elapsed_ms': 1.0})

    async def run():
        app = create_app(lambda: None, token='secret')
        monitor = app.state.monitoring
        assert isinstance(monitor, FakeSupervisor)
        assert monitor.settings.enabled is False
        assert monitor.started is False
        headers = {'Authorization': 'Bearer secret'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            aliases = await client.get('/v1/ssh/aliases', headers=headers)
            assert aliases.status_code == 200, aliases.text
            identity = await client.post('/v1/ssh/inspect', headers=headers,
                                         json={'alias': 'jetson'})
            assert identity.status_code == 200, identity.text
            tested = await client.post('/v1/ssh/test', headers=headers,
                                       json={'alias': 'jetson'})
            assert tested.status_code == 200, tested.text
            assert monitor.reconfigured is None
            saved = await client.post('/v1/ssh/target', headers=headers,
                                      json={'alias': 'jetson', 'display_name': 'Jetson'})
            assert saved.status_code == 200, saved.text
            assert monitor.reconfigured is not None
            assert monitor.reconfigured.enabled is True
            typed = await client.get('/v1/ssh/computer', headers=headers)
            assert typed.status_code == 200, typed.text
            assert typed.json()['computer']['capabilities']['monitoring'] is True
        await monitor.close()

    asyncio.run(run())
