import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from odessy.setup.contracts import DiscoveryRequest
from odessy.setup.discovery import discover


def test_discovery_is_explicit_and_offline_by_default():
    assert discover(DiscoveryRequest()).status == 'not_requested'


def test_real_http_discovery_reads_only_tags_and_preserves_names():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            payload = json.dumps({'models': [{'name': 'installed:q8', 'size': 1200}]}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(payload)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        report = discover(DiscoveryRequest(endpoint=f'http://127.0.0.1:{server.server_port}'))
        assert report.status == 'ok'
        assert report.models[0].name == 'installed:q8'
        assert requests == ['/api/tags']
    finally:
        server.shutdown(); server.server_close(); worker.join()


@pytest.mark.parametrize('url', ['http://:11434', 'http://localhost:not-a-port', 'http://localhost:99999', 'http://user:password@localhost', 'file:///tmp/model'])
def test_discovery_rejects_malformed_or_secret_bearing_endpoints(url):
    with pytest.raises(ValueError):
        DiscoveryRequest(endpoint=url)


def test_unreachable_is_not_empty_model_list():
    report = discover(DiscoveryRequest(endpoint='http://127.0.0.1:1', timeout=0.1))
    assert report.status == 'unreachable'
    assert report.models == ()


@pytest.mark.parametrize('body,expected', [
    (b'{"models":[]}', 'empty'),
    (b'not json', 'invalid_response'),
    (json.dumps({'models': [{'name': 'one', 'size': True}]}).encode(), 'invalid_response'),
    (json.dumps({'models': [{'name': str(n)} for n in range(257)]}).encode(), 'invalid_response'),
    (b'x' * (1024 * 1024 + 1), 'invalid_response'),
], ids=['empty', 'malformed', 'invalid-size', 'too-many-models', 'oversized'])
def test_model_inventory_bounds_and_invalid_shapes(body, expected):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(body)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        report = discover(DiscoveryRequest(endpoint=f'http://127.0.0.1:{server.server_port}'))
        assert report.status == expected
    finally:
        server.shutdown(); server.server_close(); worker.join()


def test_discovery_does_not_follow_redirects_to_other_services():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(302); self.send_header('Location', '/other-service'); self.end_headers()
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        assert discover(DiscoveryRequest(endpoint=f'http://127.0.0.1:{server.server_port}')).status == 'unreachable'
        assert requests == ['/api/tags']
    finally:
        server.shutdown(); server.server_close(); worker.join()
