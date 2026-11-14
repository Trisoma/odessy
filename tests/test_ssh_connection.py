"""SSH identity/config and monitoring projection; lifecycle belongs to Go."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from odessy.application.api import create_app
from odessy.monitoring.settings import MonitoringSettings
from odessy.ssh.identity import resolve_host, ssh_argv


class FakeMonitor:
    def __init__(self, *, ssh_host_id="host_" + "a" * 64, alias="jetson"):
        self.settings = SimpleNamespace(ssh_host_id=ssh_host_id, ssh_alias=alias)
        self.ssh_host_id = ssh_host_id
        self.requests = []

    async def request(self, method, path, query=None):
        self.requests.append((path, query))
        if path == "/internal/v1/hosts":
            return {"schema_version": 1, "hosts": [
                {"host_id": self.ssh_host_id, "display_name": "Jetson",
                 "fingerprint_sha256": "SHA256:test", "trust": "ssh",
                 "connectivity": "online", "monitoring_access": "allowed",
                 "generation": "ssh:SHA256:test", "sample": {
                     "observed_at": "2026-11-11T12:00:00Z",
                     "cpu": {"load_1": 0.05},
                 }},
                {"host_id": "host_" + "b" * 64, "display_name": "Paired",
                 "trust": "paired", "connectivity": "online",
                 "monitoring_access": "allowed", "generation": "1:1"},
            ], "event_cursor": "e:1", "next_cursor": None}
        if path == f"/internal/v1/hosts/{self.ssh_host_id}":
            return {"schema_version": 1, "host": {
                "host_id": self.ssh_host_id, "display_name": "Jetson",
                "fingerprint_sha256": "SHA256:test", "trust": "ssh",
                "connectivity": "online", "monitoring_access": "allowed",
                "generation": "ssh:SHA256:test", "latency_ms": 12.0,
                "sample": {"observed_at": "2026-11-11T12:00:00Z"},
                "stale_groups": [],
            }}
        return {"schema_version": 1, "status": "healthy"}

    async def events(self, cursor=None):
        yield b'id: e:2\nevent: host.updated\ndata: {"schema_version":1}\n\n'


def test_ssh_settings_use_a_go_compatible_host_id_and_opt_in():
    settings = MonitoringSettings.from_env({
        "ODESSY_SSH_MONITORING": "on",
        "ODESSY_SSH_ALIAS": "jetson",
    })
    assert settings.enabled is True
    assert settings.ssh_monitoring is True
    assert settings.ssh_host_id.startswith("host_")
    assert len(settings.ssh_host_id) == 5 + 64
    assert settings.ssh_host_id in settings.host_ids

    both = MonitoringSettings.from_env({
        "ODESSY_SSH_MONITORING": "on",
        "ODESSY_SSH_ALIAS": "jetson",
        "ODESSY_LINK_BIN": "/usr/bin/odessy-link",
    })
    assert both.link_binary == "/usr/bin/odessy-link"
    assert both.ssh_alias == "jetson"

    connection_only = MonitoringSettings.from_env({
        "ODESSY_SSH_COMPUTER": "on",
        "ODESSY_SSH_ALIAS": "jetson",
    })
    assert connection_only.enabled is True
    assert connection_only.ssh_monitoring is False
    assert connection_only.ssh_host_id not in connection_only.host_ids

    with pytest.raises(ValueError):
        MonitoringSettings.from_env({"ODESSY_SSH_MONITORING": "on"})
    with pytest.raises(ValueError):
        MonitoringSettings.from_env({
            "ODESSY_SSH_MONITORING": "on", "ODESSY_SSH_ALIAS": "jetson",
            "ODESSY_SSH_HOST_ID": "host_ssh_test"})


def test_ssh_identity_uses_the_real_configuration(tmp_path):
    ssh = shutil.which("ssh")
    if ssh is None:
        pytest.skip("OpenSSH client is unavailable")
    config = tmp_path / "config"
    config.write_text("Host demo\n  HostName 192.0.2.10\n  User tester\n", encoding="utf-8")
    host = resolve_host("demo", ssh=ssh, config_path=str(config))
    assert host.hostname == "192.0.2.10"
    assert host.user == "tester"
    argv = ssh_argv(host, "printf ok", ssh=ssh)
    assert "BatchMode=yes" in argv and "StrictHostKeyChecking=yes" in argv


def test_host_projection_keeps_pairing_and_adds_methods():
    monitor = FakeMonitor()

    async def run():
        app = create_app(lambda: None, token="secret", monitoring=monitor)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            headers = {"Authorization": "Bearer secret"}
            body = (await client.get("/v1/monitoring/hosts", headers=headers)).json()
            assert [h["host_id"] for h in body["hosts"]] == [
                monitor.ssh_host_id, "host_" + "b" * 64]
            ssh, paired = body["hosts"]
            assert ssh["trust"] == {"state": "unknown", "local_revision": "ssh"}
            assert ssh["connection"]["method"] == "ssh"
            assert paired["trust"]["state"] == "paired"
            assert paired["connection"]["method"] == "odessy-link"
            assert body["event_cursor"] == "e:1"
            assert body["next_cursor"] is None

            computer = (await client.get("/v1/ssh/computer", headers=headers)).json()["computer"]
            assert computer["method"] == "ssh"
            assert computer["capabilities"] == {
                "transport": True, "monitoring": True,
                "execution": False, "workspace": False}
            assert computer["readiness"] == "ready"

    asyncio.run(run())


def test_connection_only_projection_is_not_online_or_monitoring():
    ssh_id = "host_" + "c" * 64

    class Offline(FakeMonitor):
        async def request(self, method, path, query=None):
            if path == f"/internal/v1/hosts/{ssh_id}":
                return {"schema_version": 1, "host": {
                    "host_id": ssh_id, "display_name": "Jetson",
                    "fingerprint_sha256": "SHA256:test", "trust": "ssh",
                    "connectivity": "unknown", "monitoring_access": "disabled",
                    "generation": "ssh:SHA256:test", "sample": None,
                    "stale_groups": [],
                }}
            return await super().request(method, path, query)

    monitor = Offline(ssh_host_id=ssh_id)

    async def run():
        app = create_app(lambda: None, token="secret", monitoring=monitor)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            headers = {"Authorization": "Bearer secret"}
            computer = (await client.get("/v1/ssh/computer", headers=headers)).json()["computer"]
            assert computer["status"]["connectivity"] == "unknown"
            assert computer["status"]["monitoring_access"] == "disabled"
            assert computer["capabilities"] == {
                "transport": False, "monitoring": False,
                "execution": False, "workspace": False}
            assert computer["readiness"] == "unavailable"

    asyncio.run(run())


def test_pagination_and_shared_events_are_proxied_unchanged():
    class Paged(FakeMonitor):
        async def request(self, method, path, query=None):
            if path == "/internal/v1/hosts":
                if query and query.get("cursor"):
                    return {"schema_version": 1, "hosts": [], "event_cursor": "e:1",
                            "next_cursor": None}
                return {"schema_version": 1, "hosts": [], "event_cursor": "e:1",
                        "next_cursor": "snap:2"}
            return await super().request(method, path, query)

    monitor = Paged()

    async def run():
        app = create_app(lambda: None, token="secret", monitoring=monitor)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            headers = {"Authorization": "Bearer secret"}
            first = (await client.get("/v1/monitoring/hosts", headers=headers)).json()
            assert first["next_cursor"] == "snap:2"
            second = (await client.get("/v1/monitoring/hosts?cursor=snap:2",
                                       headers=headers)).json()
            assert second["hosts"] == []
            events = await client.get("/v1/monitoring/events", headers={
                **headers, "Last-Event-ID": "e:1"})
            assert "event: host.updated" in events.text

    asyncio.run(run())

def test_ssh_helpers_pin_stdin_to_null(monkeypatch):
    """The installed coordinator reads its own stdin; a child must not share it.

    With ``--exit-on-stdin-close`` a reader thread blocks on the private stdin pipe.
    Inheriting that handle made the real OpenSSH client return no answer within its
    bound while the same argv worked from a shell. SSH helpers must use DEVNULL.
    """

    from odessy.ssh import identity

    seen = []

    class Completed:
        returncode = 0
        stderr = ""

        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(args, **kwargs):
        seen.append(kwargs)
        if "-G" in args:
            return Completed("hostname 10.1.1.2\nuser jaimef\nport 22\n")
        return Completed("odessy-connect-ok")

    monkeypatch.setattr(identity.subprocess, "run", fake_run)
    host = identity.resolve_host("jetson")
    assert identity.probe_connection(host)["ok"] is True
    assert seen and all(kwargs.get("stdin") is identity.subprocess.DEVNULL
                        for kwargs in seen)


def test_effective_known_host_paths_with_spaces_and_hostkeyalias(tmp_path):
    import subprocess
    from odessy.ssh.identity import read_identity
    ssh, keygen = shutil.which('ssh'), shutil.which('ssh-keygen')
    if not ssh or not keygen:
        pytest.skip('OpenSSH tools unavailable')
    home = tmp_path / 'Jane  Doe'
    home.mkdir()
    key = home / 'fixture-key'
    subprocess.run([keygen, '-q', '-t', 'ed25519', '-N', '', '-f', str(key)],
                   check=True, capture_output=True)
    public = key.with_suffix('.pub').read_text().strip()
    known = home / 'known hosts'
    known.write_text('reviewed-name ' + public + '\n')
    second = home / 'other known hosts'
    second.write_text('')
    config = tmp_path / 'config'
    config.write_text('Host demo\n HostName 192.0.2.10\n User tester\n'
                      ' HostKeyAlias reviewed-name\n UserKnownHostsFile "'
                      + known.as_posix() + '" "' + second.as_posix() + '"\n')
    before = known.read_bytes()
    host = resolve_host('demo', ssh=ssh, config_path=str(config))
    assert tuple(Path(value) for value in host.known_host_files) == (known, second)
    assert host.host_key_alias == 'reviewed-name'
    identity = read_identity(host, ssh=ssh, ssh_keygen=keygen)
    assert identity.known and len(identity.public_keys) == 1
    assert ' '.join(identity.public_keys[0]) == ' '.join(public.split()[:2])
    assert known.read_bytes() == before


def test_ambiguous_known_host_paths_are_refused(tmp_path, monkeypatch):
    from odessy.ssh.identity import SshError, _known_host_paths
    monkeypatch.chdir(tmp_path)
    for name in ('a', 'b', 'a b'):
        (tmp_path / name).write_text('')
    with pytest.raises(SshError, match='Ambiguous'):
        _known_host_paths('a b')


def test_bootstrap_carries_trusted_endpoint_and_public_key(monkeypatch):
    import base64
    import hashlib
    from odessy.ssh.identity import SshHost, SshIdentity
    from odessy.monitoring.supervisor import MonitoringSupervisor
    encoded = base64.b64encode(b'fixture-public-key').decode()
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(b'fixture-public-key').digest()).decode().rstrip('=')
    host = SshHost('demo', '192.0.2.10', 'tester', port=2222)
    identity = SshIdentity(host, (('ed25519', fingerprint),), public_keys=(('ssh-ed25519', encoded),))
    monkeypatch.setattr('odessy.ssh.identity.resolve_host', lambda *a, **kw: host)
    monkeypatch.setattr('odessy.ssh.identity.read_identity', lambda *a, **kw: identity)
    monitor = MonitoringSupervisor(MonitoringSettings(ssh_alias='demo', ssh_binary='fixture-ssh'))
    config = asyncio.run(monitor._ssh_config())
    assert (config['hostname'], config['user'], config['port']) == ('192.0.2.10', 'tester', 2222)
    assert config['host_key'] == 'ssh-ed25519 ' + encoded
    assert config['fingerprint_sha256'] == fingerprint
