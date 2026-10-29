"""Coordinator-side LAN pairing: discovery, the request, and a refusal.

The mDNS module is faked through ``sys.modules`` so these tests do not need
``zeroconf`` installed; the request tests use a real loopback HTTP server, so the
URL, the body and the status mapping are exercised end to end.
"""
import asyncio
import json
import socket
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

from odessy.setup import cli, lan_pair

SERVICE_NAME = 'Jetson.' + lan_pair.SERVICE_TYPE
PAIRED = {'token': 'abc', 'url': 'http://192.168.1.10:8765'}


def _fake_zeroconf(monkeypatch, info, name=SERVICE_NAME):
    """A ServiceBrowser that reports one service the moment it is constructed."""

    class Browser:
        def __init__(self, zeroconf, service_type, listener):
            assert service_type == lan_pair.SERVICE_TYPE
            listener.add_service(zeroconf, service_type, name)

        def cancel(self):
            pass

    class Zeroconf:
        def get_service_info(self, service_type, service_name):
            assert service_name == name
            return info

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, 'zeroconf',
                        SimpleNamespace(ServiceBrowser=Browser, Zeroconf=Zeroconf))


def test_scan_returns_list(monkeypatch):
    info = SimpleNamespace(port=8765, server='jetson.local.',
                           parsed_addresses=lambda: ['192.168.1.10'])
    _fake_zeroconf(monkeypatch, info)

    hosts = lan_pair.scan_execution_hosts(timeout=0.01)

    assert hosts == [{'name': 'Jetson', 'host': '192.168.1.10', 'port': 8765}]


def test_scan_falls_back_to_the_announced_name_without_ipv4(monkeypatch):
    info = SimpleNamespace(port=8765, server='jetson.local.',
                           parsed_addresses=lambda: ['fe80::1'])
    _fake_zeroconf(monkeypatch, info)

    assert lan_pair.scan_execution_hosts(timeout=0.01) == [
        {'name': 'Jetson', 'host': 'jetson.local', 'port': 8765}]


def test_scan_without_zeroconf_returns_empty_list(monkeypatch):
    monkeypatch.setitem(sys.modules, 'zeroconf', None)  # Imports as ImportError.

    assert lan_pair.scan_execution_hosts(timeout=0.0) == []


def test_announced_service_name_matches_the_laptop(monkeypatch):
    from odessy.application.pairing import SERVICE_TYPE

    assert lan_pair.SERVICE_TYPE == SERVICE_TYPE


class _Handler(BaseHTTPRequestHandler):
    status = 200
    body = json.dumps(PAIRED).encode()
    seen = []

    def do_POST(self):
        length = int(self.headers.get('content-length', '0'))
        _Handler.seen.append((self.path, json.loads(self.rfile.read(length))))
        self.send_response(_Handler.status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(_Handler.body)))
        self.end_headers()
        self.wfile.write(_Handler.body)

    def log_message(self, *arguments):
        pass


@contextmanager
def _host(status=200, body=None):
    _Handler.status, _Handler.seen = status, []
    _Handler.body = json.dumps(PAIRED).encode() if body is None else body
    server = HTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _closed_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def test_request_pairing_accepted():
    with _host() as server:
        result = asyncio.run(lan_pair.request_pairing('127.0.0.1', server.server_port, 'Jetson'))

    assert result == PAIRED
    assert _Handler.seen == [('/v1/pairing/request', {'name': 'Jetson'})]


def test_request_pairing_declined():
    with _host(status=403, body=b'{"detail": "Pairing declined"}') as server:
        with pytest.raises(lan_pair.PairingDeclined):
            asyncio.run(lan_pair.request_pairing('127.0.0.1', server.server_port, 'Jetson'))


@pytest.mark.parametrize('status, body', [
    (500, b'{}'),
    (404, b'{}'),
    (200, b'{"url": "http://192.168.1.10:8765"}'),  # Accepted without a token.
    (200, b'not json'),
])
def test_other_answers_are_failures(status, body):
    with _host(status=status, body=body) as server:
        with pytest.raises(lan_pair.PairingFailed):
            asyncio.run(lan_pair.request_pairing('127.0.0.1', server.server_port, 'Jetson'))


def test_unreachable_host_fails_without_raising_anything_else():
    with pytest.raises(lan_pair.PairingFailed):
        asyncio.run(lan_pair.request_pairing('127.0.0.1', _closed_port(), 'Jetson'))


def test_environment_proxies_do_not_intercept_a_pairing(monkeypatch):
    # A LAN host is reached directly; a configured proxy would swallow the request.
    for name in ('http_proxy', 'HTTP_PROXY', 'https_proxy', 'HTTPS_PROXY', 'all_proxy'):
        monkeypatch.setenv(name, 'http://127.0.0.1:9')
    with _host() as server:
        result = asyncio.run(lan_pair.request_pairing('127.0.0.1', server.server_port, 'Jetson'))

    assert result == PAIRED


@pytest.mark.parametrize('host, port, name', [
    ('', 8765, 'Jetson'), ('127.0.0.1', 0, 'Jetson'), ('127.0.0.1', 70000, 'Jetson'),
    ('127.0.0.1', 8765, '   '), ('127.0.0.1', 8765, 'x' * 65)])
def test_a_request_that_could_not_be_understood_is_refused_locally(host, port, name):
    with pytest.raises(lan_pair.PairingFailed):
        asyncio.run(lan_pair.request_pairing(host, port, name))


def test_pair_host_writes_a_create_only_record(tmp_path, capsys, monkeypatch):
    with _host() as server:
        monkeypatch.setattr(cli, 'scan_execution_hosts',
                            lambda timeout: [{'name': 'Jetson', 'host': '127.0.0.1',
                                              'port': server.server_port}])
        record = tmp_path / 'odessy-pair.json'
        arguments = ['pair-host', '--host', '1', '--coordinator-name', 'Jetson',
                     '--output', str(record)]
        assert cli.main(arguments) == 0
        assert cli.main(arguments) == 2  # Never overwrites the record it made.

    written = capsys.readouterr()
    assert json.loads(written.out)['paired'] is True
    assert 'ODESSY_TOOL_SERVER_URL=http://192.168.1.10:8765' in written.err
    assert json.loads(record.read_text()) == {'tool_server_url': PAIRED['url'],
                                              'tool_server_token': PAIRED['token']}


def test_pair_host_reports_a_decline_without_writing(tmp_path, capsys, monkeypatch):
    with _host(status=403) as server:
        monkeypatch.setattr(cli, 'scan_execution_hosts',
                            lambda timeout: [{'name': 'Jetson', 'host': '127.0.0.1',
                                              'port': server.server_port}])
        record = tmp_path / 'odessy-pair.json'
        assert cli.main(['pair-host', '--host', '1', '--output', str(record)]) == 1

    assert 'declined or did not respond' in capsys.readouterr().err
    assert not record.exists()


def test_pair_host_without_a_choice_explains_itself(monkeypatch, capsys):
    monkeypatch.setattr(cli, 'scan_execution_hosts', lambda timeout: [])
    assert cli.main(['pair-host', '--non-interactive']) == 1
    assert 'No execution hosts found on this LAN.' in capsys.readouterr().err
