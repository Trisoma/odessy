"""Opt-in real Rust worker -> mTLS -> telemetry-only host, no Node/model."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / 'native/odessy-link/target/debug' / ('odessy-link.exe' if os.name == 'nt' else 'odessy-link')
pytestmark = pytest.mark.skipif(os.environ.get('ODESSY_MONITOR_E2E') != '1', reason='explicit built-Rust integration')


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
