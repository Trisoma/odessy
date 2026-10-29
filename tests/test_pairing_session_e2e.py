"""End-to-end short-code pairing through the real helper and the real binary.

This is the path the desktop uses: the Python bridge validates inputs, then runs
fixed `odessy-link pairing` commands. The exchange itself is the unapproved test
adapter, which needs both the `test-profile` cargo feature and an explicit
environment marker; when it is unavailable the test skips instead of pretending.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / 'native' / 'odessy-link' / 'target' / 'debug' / (
    'odessy-link.exe' if os.name == 'nt' else 'odessy-link')


def _adapter_available(binary, state):
    environment = dict(os.environ, ODESSY_LINK_STATE_DIR=str(state),
                       ODESSY_LINK_TEST_PAIRING_ADAPTER='1')
    completed = subprocess.run([str(binary), 'pairing', 'capabilities', '--profile', 'test-adapter'],
                               capture_output=True, text=True, env=environment, timeout=30)
    if completed.returncode != 0:
        return False
    try:
        return json.loads(completed.stdout).get('supported') is True
    except ValueError:
        return False


def _free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def _wait_for_port(port, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


@pytest.mark.skipif(not BINARY.is_file(), reason='odessy-link is not built')
def test_helper_drives_a_real_pairing_session(tmp_path, monkeypatch):
    from odessy.setup.native_bridge import pair

    laptop = tmp_path / 'laptop'
    jetson = tmp_path / 'jetson'
    laptop.mkdir()
    jetson.mkdir()
    if not _adapter_available(BINARY, laptop):
        pytest.skip('this build has no test-profile feature or the adapter marker is unset')

    # Both machines have an installation identity before anything is paired.
    for state in (laptop, jetson):
        completed = subprocess.run([str(BINARY), 'init'], capture_output=True, text=True,
                                   env=dict(os.environ, ODESSY_LINK_STATE_DIR=str(state)), timeout=60)
        assert completed.returncode == 0, completed.stderr

    workspace = tmp_path / 'workspace'
    workspace.mkdir()

    monkeypatch.setenv('ODESSY_LINK_STATE_DIR', str(laptop))
    monkeypatch.setenv('ODESSY_LINK_TEST_PAIRING_ADAPTER', '1')

    status = pair({'action': 'short-code', 'command': 'status', 'link': str(BINARY),
                   'profile': 'test-adapter'})
    assert status['supported'] is True and status['state'] == 'closed'

    began = pair({'action': 'short-code', 'command': 'begin', 'workspace': str(workspace),
                  'link': str(BINARY), 'profile': 'test-adapter'})
    assert began['state'] == 'discoverable', began
    code = began['code']
    session = began['session_id']
    assert len(code) == 6 and code.isdigit()

    port = _free_port()
    listener = subprocess.Popen(
        [str(BINARY), 'pairing', 'listen', '--listen', f'127.0.0.1:{port}',
         '--seconds', '60', '--profile', 'test-adapter'],
        env=dict(os.environ, ODESSY_LINK_STATE_DIR=str(laptop),
                 ODESSY_LINK_TEST_PAIRING_ADAPTER='1'),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert _wait_for_port(port), 'the pairing listener never accepted a connection'

        monkeypatch.setenv('ODESSY_LINK_STATE_DIR', str(jetson))
        joined = pair({'action': 'short-code', 'command': 'join',
                       'address': f'127.0.0.1:{port}', 'code': code,
                       'link': str(BINARY), 'profile': 'test-adapter'})
        assert joined['state'] == 'paired_no_access', joined
        assert code not in json.dumps(joined), 'the joining side must not echo the code'

        monkeypatch.setenv('ODESSY_LINK_STATE_DIR', str(laptop))
        waiting = pair({'action': 'short-code', 'command': 'status', 'link': str(BINARY),
                        'profile': 'test-adapter'})
        assert waiting['state'] == 'awaiting_approval', waiting
        assert waiting['peer']['fingerprint_sha256']
        token = waiting['review_token']
        assert token

        approved = pair({'action': 'short-code', 'command': 'confirm', 'session_id': session,
                         'review_token': token, 'workspace': str(workspace),
                         'link': str(BINARY), 'profile': 'test-adapter'})
        assert approved['state'] == 'ready', approved

        listed = pair({'action': 'list', 'link': str(BINARY)})
        peers = listed['peers']['peers']
        assert len(peers) == 1 and peers[0]['capability_mcp'] is True
        assert peers[0]['workspace'] == str(workspace)
    finally:
        listener.terminate()
        try:
            listener.wait(timeout=10)
        except subprocess.TimeoutExpired:
            listener.kill()

    # The pair survives a restart of the helper: state is durable, not in memory.
    restarted = pair({'action': 'short-code', 'command': 'status', 'link': str(BINARY),
                      'profile': 'test-adapter'})
    assert restarted['state'] == 'ready'

    # Cancelling a finished session changes nothing and says so.
    cancelled = pair({'action': 'short-code', 'command': 'cancel', 'session_id': session,
                      'link': str(BINARY), 'profile': 'test-adapter'})
    assert cancelled['cancelled'] is False
    assert cancelled['state'] == 'ready'
