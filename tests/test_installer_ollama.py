"""Ollama setup tests using only temporary profiles and an isolated HTTP fixture."""
from __future__ import annotations
import contextlib
import io
import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from installer.ollama import (MAX_RESPONSE_BYTES, get_json, inspect_service,
                              local_endpoint, main, proposed_config, write_config)

MODEL = 'lfm2.5-thinking:1.2b-q8_0'


def fixture_fetch(endpoint, path, timeout):
    return {'version': '0.6.8'} if path == '/api/version' else {'models': [{'name': MODEL}]}


class OllamaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def report(self, **kwargs):
        return inspect_service('http://127.0.0.1:11434', MODEL, fetch=fixture_fetch, **kwargs)

    def test_defaults_and_valid_service_do_not_claim_inference(self):
        report = self.report()
        self.assertEqual(report['service_status'], 'available')
        self.assertTrue(report['model_present'])
        self.assertFalse(report['inference_verified'])
        self.assertFalse(report['installation_ready'])
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(proposed_config(report)['ODESSY_MODEL_BACKEND'], 'lfm')

    def test_missing_model_is_distinct_from_missing_service(self):
        def fetch(endpoint, path, timeout):
            return {'version': '0.6.8'} if path == '/api/version' else {'models': []}
        report = inspect_service('http://localhost:11434', MODEL, fetch=fetch)
        self.assertEqual(report['service_status'], 'available')
        self.assertFalse(report['model_present'])
        def unavailable(*args):
            raise ConnectionRefusedError('fixture refused')
        report = inspect_service('http://localhost:11434', MODEL, fetch=unavailable)
        self.assertEqual(report['service_status'], 'unavailable')
        self.assertIn('fixture refused', report['error'])

    def test_untagged_latest_and_registry_port(self):
        def fetch(endpoint, path, timeout):
            return {'version': '0.6.8'} if path == '/api/version' else {'models': [{'name': 'localhost:5000/team/model:latest'}]}
        report = inspect_service('http://127.0.0.1', 'localhost:5000/team/model', fetch=fetch)
        self.assertTrue(report['model_present'])

    def test_malformed_version_or_tags_not_reusable(self):
        cases = [({'version': 'not-ollama'}, {'models': []}),
                 ({'version': '0.6.8'}, {}),
                 ({'version': '0.6.8'}, {'models': [None]}),
                 ({'version': '0.6.8'}, {'models': [{'name': 'bad\nname'}]})]
        for version, tags in cases:
            with self.subTest(version=version, tags=tags):
                report = inspect_service('http://127.0.0.1', MODEL,
                    fetch=lambda endpoint, path, timeout: version if path == '/api/version' else tags)
                self.assertEqual(report['service_status'], 'invalid_response')

    def test_rejects_remote_urls_credentials_paths_and_injection(self):
        for endpoint in ('https://127.0.0.1', 'http://example.com', 'http://192.168.1.2',
                         'http://user:pass@127.0.0.1', 'http://127.0.0.1/api',
                         'http://127.0.0.1?x=1', 'http://127.0.0.1#',
                         'http://127.0.0.1:0', 'http://127.0.0.1:99999', 'http://127.0.0.1\n'):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                local_endpoint(endpoint)
        self.assertEqual(local_endpoint('http://[::1]:11435/'), 'http://[::1]:11435')
        for model in ('bad\nODESSY_MODEL_BACKEND=cloud', '$HOME', '%PATH%', ''):
            with self.assertRaises(ValueError):
                inspect_service('http://localhost', model, fetch=fixture_fetch)
        for timeout in (0, -1, 31, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.report(timeout=timeout)

    def test_bad_json_probe_classified_as_invalid_response(self):
        def malformed(*args):
            raise ValueError('invalid JSON fixture')
        report = inspect_service('http://127.0.0.1', MODEL, fetch=malformed)
        self.assertEqual(report['service_status'], 'invalid_response')

    def test_missing_model_profile_does_not_claim_ready(self):
        report = self.report(); report['model_present'] = False
        target = self.root / 'pending.env'
        write_config(target, report)
        self.assertTrue(target.is_file())
        self.assertFalse(report['installation_ready'])
        self.assertFalse(report['inference_verified'])

    def test_config_round_trips_through_existing_reader(self):
        # Import only the existing config file reader; never modify core code.
        spec = importlib.util.spec_from_file_location('existing_config_reader', Path('src/odessy/config/config_file.py'))
        reader = importlib.util.module_from_spec(spec); spec.loader.exec_module(reader)
        read_env_file = reader.read_env_file
        target = self.root / 'new profile' / 'odessy.env'
        write_config(target, self.report())
        self.assertEqual(read_env_file(target), proposed_config(self.report()))
        original = target.read_bytes()
        with self.assertRaises(FileExistsError):
            write_config(target, self.report())
        self.assertEqual(target.read_bytes(), original)

    def test_service_failure_and_relative_target_do_not_write(self):
        report = self.report(); report['service_status'] = 'unavailable'
        with self.assertRaises(ValueError):
            write_config(self.root / 'absent' / 'profile.env', report)
        with self.assertRaises(ValueError):
            write_config(Path('relative.env'), self.report())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_exclusive_creation_preserves_concurrent_file(self):
        target = self.root / 'profile.env'
        original_mkdir = Path.mkdir
        def race(path, *args, **kwargs):
            result = original_mkdir(path, *args, **kwargs)
            target.write_text('concurrent owner', encoding='utf-8')
            return result
        with patch.object(Path, 'mkdir', race), self.assertRaises(FileExistsError):
            write_config(target, self.report())
        self.assertEqual(target.read_text(), 'concurrent owner')

    def test_cli_preview_json_and_exit_codes(self):
        for present, expected in ((True, 0), (False, 2)):
            report = self.report(); report['model_present'] = present
            target = self.root / 'preview' / 'odessy.env'
            output = io.StringIO()
            with patch('installer.ollama.inspect_service', return_value=report), \
                 patch.dict(os.environ, {'ODESSY_MODEL_BACKEND': 'private-value'}), \
                 contextlib.redirect_stdout(output):
                self.assertEqual(main(['--config-file', str(target), '--json']), expected)
            data = json.loads(output.getvalue())
            self.assertFalse(data['configuration']['written'])
            self.assertIn('ODESSY_MODEL_BACKEND', data['environment_overrides'])
            self.assertNotIn('private-value', output.getvalue())
            self.assertFalse(target.parent.exists())

    def test_cli_explicit_write_and_preserve_existing(self):
        target = self.root / 'profile.env'
        with patch('installer.ollama.inspect_service', side_effect=lambda *a, **k: self.report()), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--config-file', str(target), '--write-config']), 0)
            original = target.read_bytes()
            self.assertEqual(main(['--config-file', str(target), '--write-config']), 1)
        self.assertEqual(target.read_bytes(), original)


class HttpProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                cls.calls.append(self.path)
                status, body, length = 200, b'{"version":"0.6.8"}', None
                if self.path == '/api/tags':
                    body = json.dumps({'models': [{'name': MODEL}]}).encode()
                elif self.path == '/redirect':
                    status = 302
                elif self.path == '/huge':
                    length = MAX_RESPONSE_BYTES + 1
                elif self.path == '/bad-json':
                    body = b'not-json'
                elif self.path == '/array':
                    body = b'[]'
                elif self.path == '/truncated':
                    length = len(body) + 20
                elif self.path == '/huge-unannounced':
                    body = b'x' * (MAX_RESPONSE_BYTES + 1)
                if self.path == '/slow':
                    time.sleep(0.2)
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                if self.path != '/huge-unannounced':
                    self.send_header('Content-Length', str(length if length is not None else len(body)))
                if status == 302:
                    self.send_header('Location', '/must-not-follow')
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (ConnectionError, OSError):
                    pass
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.endpoint = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def test_real_http_gets_ignore_proxy_and_never_generate_or_pull(self):
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:1', 'http_proxy': 'http://127.0.0.1:1'}):
            report = inspect_service(self.endpoint, MODEL)
        self.assertEqual(report['service_status'], 'available')
        self.assertTrue(report['model_present'])
        self.assertEqual(self.calls[-2:], ['/api/version', '/api/tags'])

    def test_slow_response_times_out(self):
        with self.assertRaises(TimeoutError):
            get_json(self.endpoint, '/slow', 0.05)

    def test_redirect_size_truncation_and_json_failures(self):
        for path in ('/redirect', '/huge', '/bad-json', '/array', '/truncated', '/huge-unannounced'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                get_json(self.endpoint, path, 2)
        self.assertNotIn('/must-not-follow', self.calls)


if __name__ == '__main__':
    unittest.main()
