"""Opt-in real Go service -> real Rust worker -> local metrics -> Python REST/SSE."""
import asyncio
import json
import os
from pathlib import Path
import subprocess

import httpx
import pytest

from odessy.application.api import create_app
from odessy.monitoring.settings import MonitoringSettings
from odessy.monitoring.supervisor import MonitoringSupervisor

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.environ.get('ODESSY_MONITOR_E2E') != '1', reason='explicit real monitoring integration')


def test_real_go_rust_python_rest_sse_and_shutdown(tmp_path, monkeypatch):
    binary = ROOT.parent / '.qualification/odessy-monitor.exe'
    link = ROOT / 'native/odessy-link/target/debug/odessy-link.exe'
    monkeypatch.setenv('ODESSY_LINK_STATE_DIR', str(tmp_path / 'trust'))
    subprocess.run([str(link), 'init'], check=True, capture_output=True, timeout=10)
    settings = MonitoringSettings(True, (), True, 'default', str(binary), str(link), str(tmp_path / 'history'))

    async def run():
        monitor = MonitoringSupervisor(settings)
        try:
            await monitor.start()
            app = create_app(lambda: None, token='public-token', monitoring=monitor)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                headers = {'Authorization': 'Bearer public-token'}
                assert (await client.get('/v1/monitoring')).status_code == 401
                for _ in range(25):
                    response = await client.get('/v1/monitoring/hosts', headers=headers)
                    assert response.status_code == 200, response.text
                    hosts = response.json()['hosts']
                    if hosts and (hosts[0].get('sample') or {}).get('memory'):
                        break
                    await asyncio.sleep(.5)
                assert hosts, response.text
                host = hosts[0]
                metrics = await client.get(f"/v1/monitoring/hosts/{host['host_id']}/metrics", headers=headers)
                assert metrics.status_code == 200, metrics.text
                assert metrics.json()['sample']['memory']['total_bytes'] > 0, metrics.text
                missing = await client.get('/v1/monitoring/hosts/host_' + 'f' * 64, headers=headers)
                assert missing.status_code == 404
                stream = await client.get('/v1/monitoring/events', headers={**headers, 'Last-Event-ID': 'expired:0'})
                assert 'resync_required' in stream.text
                assert monitor.token not in json.dumps([response.json(), metrics.json(), stream.text])
        finally:
            await monitor.close()
        assert monitor.process is None
    asyncio.run(run())
