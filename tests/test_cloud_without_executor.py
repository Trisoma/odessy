"""A real local mock cloud provider through the application/session path; no paid API."""
import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import httpx


def test_cloud_chat_survives_a_stopped_execution_host(tmp_path, monkeypatch):
    from odessy import cli
    from odessy.application.api import create_app
    from odessy.application.bootstrap import create_controller
    from odessy.models.cloud_model import build_cloud_model
    from odessy.tools import lan_executor
    requests = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(data)
            assert self.headers['Authorization'] == 'Bearer dummy-local-test-key'
            payload = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'Cloud chat still works'}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    monkeypatch.setattr(cli, 'node_tool_server_dependencies_installed', lambda: False)
    lan_executor.select(lan_executor.LanConnection('offline', 'Stopped executor', 'http://127.0.0.1:9/mcp', '/remote', 'dummy-executor-key'))
    async def run():
        model = build_cloud_model('custom', {'ODESSY_CLOUD_API_KEY': 'dummy-local-test-key',
            'ODESSY_CLOUD_MODEL': 'local-mock', 'ODESSY_CLOUD_BASE_URL': f'http://127.0.0.1:{provider.server_port}/v1'})
        controllers = []
        async def factory():
            controller = await create_controller(workspace=tmp_path, model=model)
            controllers.append(controller)
            return controller
        app = create_app(factory, token='local-app-key')
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                         headers={'Authorization': 'Bearer local-app-key'}) as client:
                response = await asyncio.wait_for(client.post('/v1/sessions'), 3)
                assert response.status_code == 201
                sid = response.json()['id']
                assert (await client.post(f'/v1/sessions/{sid}/turns', json={'text': 'Hello'})).status_code == 202
                for _ in range(100):
                    snapshot = (await client.get(f'/v1/sessions/{sid}')).json()
                    if snapshot['history']:
                        break
                    await asyncio.sleep(.05)
                assert snapshot['history'][-1]['assistant'] == 'Cloud chat still works'
                assert requests
        finally:
            for controller in controllers:
                await controller.close()
    try:
        asyncio.run(run())
    finally:
        lan_executor.clear()
        provider.shutdown()
        provider.server_close()
