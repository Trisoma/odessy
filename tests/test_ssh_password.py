"""The password remains in a bounded process-local broker, never in argv/config."""
import urllib.error
import urllib.request

import pytest


def test_broker_requires_exact_token_and_expires_on_close():
    from odessy.ssh.password import PasswordLease
    lease = PasswordLease('fixture-password')
    url, token = lease.url, lease.token
    with pytest.raises(urllib.error.HTTPError) as denied:
        urllib.request.urlopen(url, timeout=2)
    assert denied.value.code == 403
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(request, timeout=2) as response:
        assert response.read() == b'fixture-password'
    assert 'fixture-password' not in repr(lease)
    lease.close()
    assert lease.password == ''
    with pytest.raises((OSError, urllib.error.URLError)):
        urllib.request.urlopen(request, timeout=1)


def test_password_probe_keeps_secret_out_of_command_arguments(monkeypatch):
    import subprocess
    from odessy.ssh.password import PasswordLease
    from odessy.ssh.identity import SshHost, probe_connection
    def run(args, **options):
        assert 'fixture-password' not in repr(args) + repr(options)
        assert 'BatchMode=no' in args
        assert options['env']['SSH_ASKPASS_REQUIRE'] == 'force'
        assert options['env']['ODESSY_SSH_ASKPASS'] == '1'
        assert options['env']['SSH_ASKPASS'] == '/trusted/odessy-monitor'
        return subprocess.CompletedProcess(args, 0, 'odessy-connect-ok', '')
    monkeypatch.setattr(subprocess, 'run', run)
    with PasswordLease('fixture-password') as lease:
        result = probe_connection(SshHost('jetson', '10.1.1.2', 'jaimef'),
                                  password_lease=lease, askpass='/trusted/odessy-monitor')
    assert result['ok'] is True


@pytest.mark.parametrize('value', ['', 'x' * 1025, 'a\nb', 'a\rb', 'a\x00b', None])
def test_invalid_password_is_rejected_before_broker(value):
    from odessy.ssh.password import PasswordLease
    with pytest.raises(ValueError):
        PasswordLease(value)


def test_expired_password_is_refused_by_broker_and_environment():
    import time
    from odessy.ssh.password import PasswordLease
    with PasswordLease('fixture-password') as lease:
        lease.expires = time.monotonic() - 1
        with pytest.raises(ValueError, match='expired'):
            lease.environment('/trusted/helper')
        request = urllib.request.Request(lease.url, headers={'Authorization': 'Bearer ' + lease.token})
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(request, timeout=2)
        assert denied.value.code == 403
    lease.close()  # Idempotent, including after timer/session cleanup.
    assert not lease.thread.is_alive()


def test_native_password_helper_success_denial_and_cleanup():
    import os
    from pathlib import Path
    import subprocess
    from odessy.ssh.password import PasswordLease
    helper = os.environ.get('ODESSY_ASKPASS_BINARY')
    if not helper:
        pytest.skip('Set ODESSY_ASKPASS_BINARY to a source-matched native monitor')
    assert Path(helper).is_absolute() and Path(helper).is_file()
    with PasswordLease('fixture-password') as lease:
        env = lease.environment(helper)
        result = subprocess.run([helper, "tester@fixture's password:"], env=env,
                                capture_output=True, timeout=5)
        assert result.returncode == 0
        assert result.stdout == b'fixture-password\n'
        assert result.stderr == b''
        denied = subprocess.run([helper], env={**env, 'ODESSY_SSH_PASSWORD_TOKEN': 'x' * 43},
                                capture_output=True, timeout=5)
        assert denied.returncode == 1 and not denied.stdout and not denied.stderr
    closed = subprocess.run([helper], env=env, capture_output=True, timeout=5)
    assert closed.returncode == 1 and not closed.stdout and not closed.stderr
