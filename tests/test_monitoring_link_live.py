"""Opt-in real Rust worker -> mTLS -> telemetry-only host, no Node/model."""
import asyncio
import json
import hashlib
import os
from pathlib import Path
import socket
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('ODESSY_TEST_LINK_BIN', ROOT / 'native/odessy-link/target/debug' / ('odessy-link.exe' if os.name == 'nt' else 'odessy-link')))
pytestmark = pytest.mark.skipif(os.environ.get('ODESSY_MONITOR_E2E') != '1', reason='explicit built-Rust integration')


def test_real_reviewed_enrollment_rejects_changed_bundle_and_slot(tmp_path, monkeypatch):
    from odessy.setup.native_bridge import pair
    def cli(state, *args, check=True):
        return subprocess.run([str(BIN), *args], env=dict(os.environ, ODESSY_LINK_STATE_DIR=str(state)),
                              capture_output=True, text=True, timeout=10, check=check)
    first, second, local = tmp_path / 'first', tmp_path / 'second', tmp_path / 'local'
    for state in (first, second, local): cli(state, 'init')
    first_bundle, second_bundle = tmp_path / 'first.json', tmp_path / 'second.json'
    cli(first, 'identity', 'export', '--out', str(first_bundle))
    cli(second, 'identity', 'export', '--out', str(second_bundle))
    first_fingerprint = json.loads(first_bundle.read_text())['fingerprint_sha256']
    second_fingerprint = json.loads(second_bundle.read_text())['fingerprint_sha256']
    monkeypatch.setenv('ODESSY_LINK_STATE_DIR', str(local))
    params = {'action': 'enroll', 'link': str(BIN.resolve()), 'alias': 'peer',
              'certificate': str(first_bundle), 'fingerprint': first_fingerprint}
    review = pair({**params, 'review': True})
    raw = first_bundle.read_bytes(); first_bundle.write_bytes(raw + b' ')
    assert cli(local, 'pair', 'add', '--alias', 'peer', '--certificate', str(first_bundle),
        '--expect-sha256', first_fingerprint, '--expect-bundle-sha256', hashlib.sha256(raw).hexdigest(),
        '--expect-existing', 'absent', '--expect-revision', '0', check=False).returncode != 0
    with pytest.raises(ValueError): pair({**params, 'confirm': review['plan_id']})
    first_bundle.write_bytes(raw)
    cli(local, 'pair', 'add', '--alias', 'peer', '--certificate', str(second_bundle), '--expect-sha256', second_fingerprint)
    with pytest.raises(ValueError): pair({**params, 'confirm': review['plan_id']})
    snapshot = json.loads(cli(local, 'peers', 'list', '--json').stdout)
    stale = cli(local, 'pair', 'add', '--alias', 'peer', '--certificate', str(first_bundle),
        '--expect-sha256', first_fingerprint, '--expect-bundle-sha256', hashlib.sha256(raw).hexdigest(),
        '--expect-existing', 'absent', '--expect-revision', str(snapshot['grant_revision']), check=False)
    assert stale.returncode != 0
    assert json.loads(cli(local, 'peers', 'list', '--json').stdout)['peers'][0]['fingerprint_sha256'] == second_fingerprint
    review = pair({**params, 'review': True})
    pair({**params, 'confirm': review['plan_id']})
    assert json.loads(cli(local, 'peers', 'list', '--json').stdout)['peers'][0]['fingerprint_sha256'] == first_fingerprint


def test_real_selective_revoke_through_reviewed_local_bridge(tmp_path, monkeypatch):
    from odessy.setup.native_bridge import pair
    def cli(state, *args):
        return subprocess.run([str(BIN), *args], env=dict(os.environ, ODESSY_LINK_STATE_DIR=str(state)),
                              capture_output=True, text=True, timeout=10, check=True)
    peer_state, local_state = tmp_path / 'peer', tmp_path / 'local'
    cli(peer_state, 'init'); cli(local_state, 'init')
    bundle = tmp_path / 'peer.json'
    cli(peer_state, 'identity', 'export', '--out', str(bundle))
    fingerprint = json.loads(bundle.read_text())['fingerprint_sha256']
    cli(local_state, 'pair', 'add', '--alias', 'peer', '--certificate', str(bundle), '--expect-sha256', fingerprint)
    cli(local_state, 'peers', 'grant', 'peer', '--capability', 'mcp', '--workspace', str(tmp_path))
    cli(local_state, 'peers', 'grant', 'peer', '--capability', 'telemetry.read')
    monkeypatch.setenv('ODESSY_LINK_STATE_DIR', str(local_state))
    params = {'action': 'revoke-telemetry', 'link': str(BIN.resolve()), 'alias': 'peer'}
    review = pair({**params, 'review': True})
    assert pair({**params, 'confirm': review['plan_id']})['grants_changed'] is True
    state = json.loads(cli(local_state, 'peers', 'list', '--json').stdout)['peers'][0]
    assert not state['capability_telemetry'] and state['capability_mcp'] and state['workspace'] == str(tmp_path)
    with pytest.raises(ValueError): pair({**params, 'confirm': review['plan_id']})
    params['action'] = 'revoke-execution'
    review = pair({**params, 'review': True})
    assert pair({**params, 'confirm': review['plan_id']})['grants_changed'] is True
    state = json.loads(cli(local_state, 'peers', 'list', '--json').stdout)['peers'][0]
    assert not state['capability_telemetry'] and not state['capability_mcp'] and state['workspace'] is None


def test_real_reviewed_grant_and_full_revoke_reject_peer_replacement(tmp_path, monkeypatch):
    from odessy.setup.native_bridge import pair
    def cli(state, *args):
        return subprocess.run([str(BIN), *args], env=dict(os.environ, ODESSY_LINK_STATE_DIR=str(state)),
                              capture_output=True, text=True, timeout=10, check=True)
    old, replacement, local = tmp_path / 'old', tmp_path / 'new', tmp_path / 'local'
    for state in (old, replacement, local): cli(state, 'init')
    first, second = tmp_path / 'first.json', tmp_path / 'second.json'
    cli(old, 'identity', 'export', '--out', str(first)); cli(replacement, 'identity', 'export', '--out', str(second))
    def enroll(bundle):
        fingerprint = json.loads(bundle.read_text())['fingerprint_sha256']
        cli(local, 'pair', 'add', '--alias', 'peer', '--certificate', str(bundle), '--expect-sha256', fingerprint)
    enroll(first)
    monkeypatch.setenv('ODESSY_LINK_STATE_DIR', str(local))
    inspected = json.loads(cli(local, 'peers', 'list', '--json').stdout)
    params = {'action': 'grant-telemetry', 'link': str(BIN.resolve()), 'alias': 'peer',
              'expected_peer_fingerprint': inspected['peers'][0]['fingerprint_sha256'],
              'expected_peer_revision': inspected['grant_revision']}
    old_review = pair({**params, 'review': True})
    enroll(second)
    with pytest.raises(ValueError): pair({**params, 'confirm': old_review['plan_id']})
    with pytest.raises(ValueError): pair({**params, 'review': True})
    inspected = json.loads(cli(local, 'peers', 'list', '--json').stdout)
    assert not inspected['peers'][0]['capability_telemetry']
    params.update(expected_peer_fingerprint=inspected['peers'][0]['fingerprint_sha256'],
                  expected_peer_revision=inspected['grant_revision'])
    review = pair({**params, 'review': True})
    assert pair({**params, 'confirm': review['plan_id']})['grants_changed'] is True
    inspected = json.loads(cli(local, 'peers', 'list', '--json').stdout)
    params.update(expected_peer_revision=inspected['grant_revision'])
    params.update(action='grant', workspace=str(tmp_path))
    review = pair({**params, 'review': True})
    assert pair({**params, 'confirm': review['plan_id']})['grants_changed'] is True
    state = json.loads(cli(local, 'peers', 'list', '--json').stdout)['peers'][0]
    assert state['capability_mcp'] and state['capability_telemetry']
    inspected = json.loads(cli(local, 'peers', 'list', '--json').stdout)
    params.update(expected_peer_revision=inspected['grant_revision'])
    params['action'] = 'revoke'
    review = pair({**params, 'review': True})
    assert pair({**params, 'confirm': review['plan_id']})['grants_changed'] is True
    state = json.loads(cli(local, 'peers', 'list', '--json').stdout)['peers'][0]
    assert not state['capability_telemetry'] and not state['capability_mcp'] and state['workspace'] is None


def test_real_worker_mtls_telemetry_and_revocation_without_execution(tmp_path):
    def cli(state, *args):
        env = dict(os.environ, ODESSY_LINK_STATE_DIR=str(state))
        return subprocess.run([str(BIN), *args], env=env, capture_output=True, text=True, timeout=10, check=True)
    a, b = tmp_path / 'a', tmp_path / 'b'
    cli(a, 'init'); cli(b, 'init')
    bundle_a, bundle_b = tmp_path / 'a.json', tmp_path / 'b.json'
    cli(a, 'identity', 'export', '--out', str(bundle_a))
    cli(b, 'identity', 'export', '--out', str(bundle_b))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    for state, alias, bundle in [(a, 'remote', bundle_b), (b, 'coordinator', bundle_a)]:
        fp = json.loads(bundle.read_text())['fingerprint_sha256']
        cli(state, 'pair', 'add', '--alias', alias, '--certificate', str(bundle), '--expect-sha256', fp,
            '--address', f'127.0.0.1:{port}')
        cli(state, 'peers', 'grant', alias, '--capability', 'telemetry.read')
        peers = json.loads(cli(state, 'peers', 'list', '--json').stdout)['peers']
        assert not peers[0]['capability_mcp'] and peers[0]['workspace'] is None

    async def run():
        server = await asyncio.create_subprocess_exec(str(BIN), 'serve', '--listen', f'127.0.0.1:{port}', '--telemetry-only',
            env=dict(os.environ, ODESSY_LINK_STATE_DIR=str(b)), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        worker = None
        try:
            ready = await asyncio.wait_for(server.stderr.readline(), 5)
            assert b'listening' in ready
            worker = await asyncio.create_subprocess_exec(str(BIN), 'telemetry', 'worker',
                env=dict(os.environ, ODESSY_LINK_STATE_DIR=str(a)), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            async def request(value):
                worker.stdin.write(json.dumps({'schema_version': 1, **value}).encode() + b'\n')
                await worker.stdin.drain()
                return json.loads(await asyncio.wait_for(worker.stdout.readline(), 5))
            inventory = await request({'id': '1', 'op': 'inventory'})
            assert inventory['ok'], inventory
            peer = next(h for h in inventory['result']['hosts'] if h['alias'] == 'remote')
            ref = {k: peer[k] for k in ('host_id', 'generation')}
            beat = await request({'id': '2', 'op': 'heartbeat', **ref})
            assert beat['ok'], beat
            sample = await request({'id': '3', 'op': 'sample', 'groups': ['cpu', 'memory', 'gpu'], **ref})
            assert sample['ok'], sample
            assert sample['result']['memory']['total_bytes'] > 0
            assert sample['result']['gpu']['status'] == 'unsupported'
            # Remote authority is independent of the coordinator's unchanged revision.
            await asyncio.to_thread(cli, b, 'peers', 'revoke', 'coordinator')
            denied = await request({'id': '4', 'op': 'heartbeat', **ref})
            assert not denied['ok']
            await asyncio.to_thread(cli, a, 'peers', 'revoke', 'remote')
            stale = await request({'id': '5', 'op': 'sample', 'groups': ['memory'], **ref})
            assert not stale['ok']
        finally:
            for proc in (worker, server):
                if proc and proc.returncode is None:
                    proc.kill()
                    await asyncio.wait_for(proc.communicate(), 5)
    asyncio.run(run())
