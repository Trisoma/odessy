"""Direct targets, Git-only endpoints and ephemeral SSH authentication."""
import shutil
import subprocess

import pytest

from odessy.ssh.identity import SshHost, probe_connection, resolve_host


def test_direct_user_host_works_without_creating_an_alias(tmp_path):
    binary = shutil.which('ssh')
    if not binary:
        pytest.skip('OpenSSH unavailable')
    config = tmp_path / 'config'
    config.write_text('Host *\n  BatchMode yes\n')
    host = resolve_host('jaimef@10.1.1.2', ssh=binary, config_path=str(config))
    assert (host.user, host.hostname, host.port) == ('jaimef', '10.1.1.2', 22)


def test_github_transport_is_not_reported_as_failed_authentication(monkeypatch):
    def github(args, **options):
        # A remote command produces GitHub's unrelated 'Invalid command' error.
        assert args[-1] == 'github.com'
        assert 'printf odessy-connect-ok' not in args
        return subprocess.CompletedProcess(args, 1, '',
            "Hi user! You've successfully authenticated, but GitHub does not provide shell access.")
    monkeypatch.setattr(subprocess, 'run', github)
    result = probe_connection(SshHost('github.com', 'ssh.github.com', 'git', 443))
    assert result['code'] == 'SSH_NO_SHELL'
    assert result['authenticated'] is True
    assert result['ok'] is False


def test_key_failure_explains_password_and_agent_choices(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(
        a[0], 255, '', 'Permission denied (publickey,password).'))
    result = probe_connection(SshHost('jetson', '10.1.1.2', 'jaimef'))
    assert result['code'] == 'SSH_AUTH_FAILED'
    assert 'password' in result['message'].lower()
    assert 'agent' in result['message'].lower()


def test_missing_ssh_executable_is_structured_failure(monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError('fixture')
    monkeypatch.setattr(subprocess, 'run', unavailable)
    result = probe_connection(SshHost('jetson', '10.1.1.2', 'jaimef'))
    assert result['ok'] is False
    assert result['code'] == 'SSH_NOT_AVAILABLE'


def test_successful_login_hostname_is_bounded_display_metadata(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, 'odessy-connect-ok\nubuntu-jetson\n', ''))
    assert probe_connection(SshHost('test', '192.0.2.10', 'user'))['hostname'] == 'ubuntu-jetson'
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(
        a[0], 0, 'odessy-connect-ok\n\x1b[31mhost\n', ''))
    assert probe_connection(SshHost('test', '192.0.2.10', 'user'))['hostname'] == ''


def test_explicit_port_reaches_openssh_resolution_and_probe(tmp_path):
    from odessy.ssh.identity import ssh_argv
    binary = shutil.which('ssh')
    if not binary:
        pytest.skip('OpenSSH unavailable')
    config = tmp_path / 'config'
    config.write_text('Host *\n  Port 22\n')
    host = resolve_host('tester@192.0.2.10', port=2222, ssh=binary, config_path=str(config))
    assert host.port == 2222
    args = ssh_argv(host, 'printf ok', ssh=binary)
    assert args[args.index('-p') + 1] == '2222'


def test_nondefault_port_never_trusts_default_port_key(tmp_path, monkeypatch):
    from odessy.ssh.identity import read_identity, SshError
    lookups = []
    def keygen(args, **kwargs):
        lookups.append(args[args.index('-F') + 1])
        return subprocess.CompletedProcess(args, 1, '', '')
    monkeypatch.setattr(subprocess, 'run', keygen)
    with pytest.raises(SshError, match='not in known_hosts'):
        read_identity(SshHost('test', '192.0.2.10', 'tester', 2222,
                             known_host_files=(str(tmp_path / 'known_hosts'),)))
    assert lookups == ['[192.0.2.10]:2222']


def test_revoked_host_key_is_not_reintroduced_as_trusted(monkeypatch):
    from odessy.ssh.identity import read_identity, SshError
    def keygen(args, **kwargs):
        output = ('# Host fixture found: line 1 (RSA)\nfixture rsa SHA256:fixture\n'
                  if '-l' in args else '@revoked fixture ssh-ed25519 Zml4dHVyZQ==\n')
        return subprocess.CompletedProcess(args, 0, output, '')
    monkeypatch.setattr(subprocess, 'run', keygen)
    with pytest.raises(SshError, match='revoked'):
        read_identity(SshHost('fixture', 'fixture', 'user'))
