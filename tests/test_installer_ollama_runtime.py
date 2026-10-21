"""Private Ollama preparation and lifecycle tests; no real model or large download."""
from __future__ import annotations
import contextlib
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from installer import ollama_runtime as runtime
from installer.manifest import Artifact
from installer.download import IntegrityError
from installer.unpack import ArchiveError, relative_member, unpack


class OllamaRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'private runtime'
        self.cache = self.root / 'cache'
        self.models = self.root / 'models'
        self.state = self.root / 'state'
        self.archive = self.root / 'fixture.zip'
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.writestr('ollama.exe', b'fixture-cli')
            archive.writestr('lib/ollama/LICENSE', b'fixture-license')
        artifact = Artifact('ollama-runtime', '0.40.6',
            'https://github.com/ollama/ollama/releases/download/v0.40.6/ollama-windows-amd64.zip',
            self.archive.stat().st_size, hashlib.sha256(self.archive.read_bytes()).hexdigest())
        manifest = {'schema_version': 1, 'release': 'ollama-test', 'platform': 'windows-x64',
                    'artifacts': [artifact.__dict__]}
        content = (json.dumps(manifest) + '\n').encode()
        (self.root / 'downloads.json').write_bytes(content)
        self.resources = self.root / 'resources.json'
        self.resources.write_text(json.dumps({'schema_version': 1, 'platform': 'windows-x64',
            'download_manifest': 'downloads.json', 'download_manifest_sha256': hashlib.sha256(content).hexdigest(),
            'unpacked_bytes': len(b'fixture-cli') + len(b'fixture-license'), 'files': 2,
            'sources': {'release': 'https://github.com/ollama/ollama/releases/tag/v0.40.6',
                        'checksum': 'https://github.com/ollama/ollama/releases/expanded_assets/v0.40.6',
                        'layout': 'isolated fixture'}}), encoding='utf-8')
        self.patches = [patch.object(runtime, 'require_windows'), patch('builtins.print')]
        for item in self.patches:
            item.start(); self.addCleanup(item.stop)
        self.processes = []
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill(); process.wait(timeout=5)

    def prepare(self, **kwargs):
        return runtime.prepare_runtime(self.runtime, self.cache, resources_path=self.resources,
            download=lambda *a: SimpleNamespace(path=self.archive), probe=lambda *a: None, **kwargs)

    def unused_endpoint(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            return f'http://127.0.0.1:{probe.getsockname()[1]}'

    def session(self, endpoint, **kwargs):
        return runtime.managed_service(self.runtime, self.models, self.state, endpoint, 'fixture:latest',
                                       resources_path=self.resources, **kwargs)

    def test_prepare_preserves_archive_layout_and_validates_snapshot(self):
        marker = self.prepare()
        self.assertTrue((self.runtime / 'cli/ollama.exe').is_file())
        self.assertTrue((self.runtime / 'cli/lib/ollama/LICENSE').is_file())
        self.assertEqual(marker['version'], '0.40.6')
        manifest, resources = runtime.load_release(self.resources)
        runtime.validate_installation(self.runtime, manifest, resources)
        self.assertFalse(self.models.exists())
        self.assertFalse(self.state.exists())
        (self.runtime / 'cli/lib/ollama/LICENSE').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            runtime.validate_installation(self.runtime, manifest, resources)

    def test_existing_output_is_preserved(self):
        self.runtime.mkdir(); (self.runtime / 'keep').write_text('mine')
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual((self.runtime / 'keep').read_text(), 'mine')

    def test_failed_download_does_not_create_runtime(self):
        def fail(*args): raise OSError('fixture network failure')
        with self.assertRaises(OSError):
            runtime.prepare_runtime(self.runtime, self.cache, resources_path=self.resources, download=fail)
        self.assertFalse(self.runtime.exists())

    def test_corrupt_archive_never_executes(self):
        self.archive.write_bytes(b'corrupt')
        with patch.object(runtime, 'probe_version') as probe, self.assertRaises(IntegrityError):
            self.prepare()
        probe.assert_not_called()
        self.assertFalse(self.runtime.exists())

    def test_wrong_file_count_does_not_promote(self):
        data = json.loads(self.resources.read_text()); data['files'] = 3
        self.resources.write_text(json.dumps(data))
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.runtime.exists())

    def test_probe_failure_cleans_staging(self):
        def fail(*args): raise ValueError('bad version')
        with self.assertRaises(ValueError):
            runtime.prepare_runtime(self.runtime, self.cache, resources_path=self.resources,
                download=lambda *a: SimpleNamespace(path=self.archive), probe=fail)
        self.assertFalse(self.runtime.exists())
        self.assertFalse(list(self.root.glob('.odessy-ollama-*')))

    def test_resource_metadata_changes_are_rejected(self):
        (self.root / 'downloads.json').write_text('{}')
        with self.assertRaises(ValueError): runtime.load_release(self.resources)

    def test_not_enough_space_does_not_download(self):
        with patch.object(runtime, 'runtime_budget', return_value=[{'sufficient_for_subset': False}]), \
             patch.object(runtime, 'download_artifact') as download, self.assertRaises(ValueError):
            self.prepare()
        download.assert_not_called()
        self.assertFalse(self.runtime.exists())

    def test_child_environment_is_local_and_does_not_mutate_parent(self):
        with patch.dict(os.environ, {'OLLAMA_HOST': 'http://0.0.0.0:11434', 'OLLAMA_MODELS': 'external',
                                    'OLLAMA_CLOUD': 'yes', 'USERPROFILE': 'original-profile'}):
            env = runtime.service_env('http://localhost:11435', self.models, self.state / 'home')
            self.assertEqual(env['OLLAMA_HOST'], 'http://127.0.0.1:11435')
            self.assertEqual(env['OLLAMA_NO_CLOUD'], '1')
            self.assertNotIn('OLLAMA_CLOUD', env)
            self.assertEqual(env['USERPROFILE'], str(self.state / 'home'))
            self.assertEqual(os.environ['USERPROFILE'], 'original-profile')
            self.assertEqual(os.environ['OLLAMA_MODELS'], 'external')

    def test_existing_service_is_reused_without_files_or_process(self):
        report = {'service_status': 'available', 'version': 'another-existing-version'}
        with patch.object(runtime, 'subprocess') as subprocess_mock,patch.object(runtime,'ProcessTree') as tree_factory:
            with self.session(self.unused_endpoint(), inspect=lambda *a, **k: report,
                              popen=subprocess_mock.Popen,tree_factory=tree_factory) as session:
                self.assertTrue(session['reused'])
            subprocess_mock.Popen.assert_not_called()
            tree_factory.assert_not_called()
        self.assertFalse(self.state.exists()); self.assertFalse(self.models.exists())

    def test_occupied_other_service_port_is_preserved(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            endpoint = f'http://127.0.0.1:{listener.getsockname()[1]}'
            with self.assertRaisesRegex(ValueError, 'occupied'):
                with self.session(endpoint, inspect=lambda *a, **k: {'service_status': 'invalid_response'}):
                    self.fail('Must not start')
            self.assertGreaterEqual(listener.fileno(), 0)
        self.assertFalse(self.state.exists())

    def test_nested_paths_and_relative_paths_rejected(self):
        for models, state in ((self.runtime / 'models', self.state), (self.models, self.models),
                              (Path('relative'), self.state)):
            with self.assertRaises(ValueError): runtime.check_directories(self.runtime, models, state)

    def test_foreign_state_directory_preserved(self):
        self.prepare(); self.state.mkdir(); (self.state / 'keep').write_text('mine')
        with self.assertRaises((OSError, ValueError)):
            with self.session(self.unused_endpoint(), inspect=lambda *a, **k: {'service_status': 'unavailable'}):
                self.fail('Must not use foreign state')
        self.assertEqual((self.state / 'keep').read_text(), 'mine')

    def test_failed_spawn_unlocks_without_removing_logs_or_models(self):
        self.prepare()
        def fail(*a, **k): raise OSError('cannot spawn')
        with self.assertRaises(OSError):
            with self.session(self.unused_endpoint(), inspect=lambda *a, **k: {'service_status': 'unavailable'}, popen=fail):
                self.fail('Must not report ready')
        self.assertFalse((self.state / 'serve.lock').exists())
        self.assertTrue((self.state / 'server.log').is_file())
        self.assertTrue(self.runtime.exists())

    def test_stale_lock_preserved(self):
        self.prepare(); self.state.mkdir()
        (self.state / runtime.STATE_MARKER).write_text(json.dumps({'schema_version': 1, 'runtime_dir': str(self.runtime.resolve())}))
        lock = self.state / 'serve.lock'; lock.write_text('old owner')
        with self.assertRaisesRegex(ValueError, 'session lock'):
            with self.session(self.unused_endpoint(), inspect=lambda *a, **k: {'service_status': 'unavailable'}):
                self.fail('Must not start duplicate session')
        self.assertEqual(lock.read_text(), 'old owner')

    def test_real_child_readiness_and_context_cleanup(self):
        self.prepare()
        script = self.root / 'fixture_server.py'
        script.write_text('''import json,os
from http.server import BaseHTTPRequestHandler,HTTPServer
from urllib.parse import urlsplit
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  body=json.dumps({'version':'0.40.6'} if self.path=='/api/version' else {'models':[]}).encode()
  self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
url=urlsplit(os.environ['OLLAMA_HOST'])
HTTPServer((url.hostname,url.port),Handler).serve_forever()
''', encoding='utf-8')
        def spawn(command, **kwargs):
            self.assertEqual(command, [str(self.runtime / 'cli/ollama.exe'), 'serve'])
            process = subprocess.Popen([sys.executable, '-I', str(script)], **kwargs)
            self.processes.append(process)
            return process
        endpoint = self.unused_endpoint()
        with self.session(endpoint, popen=spawn, startup_timeout=5) as session:
            self.assertFalse(session['reused'])
            self.assertFalse(session['report']['model_present'])
            self.assertIsNone(session['process'].poll())
        self.assertIsNotNone(session['process'].poll())
        # A Windows venv launcher can exit while its interpreter/server survives.
        # The owned job must be empty before returning, not just the parent dead.
        self.assertEqual(runtime.inspect_service(endpoint,'fixture:latest',timeout=.2)['service_status'],'unavailable')
        self.assertFalse((self.state / 'serve.lock').exists())
        self.assertTrue((self.state / 'server.log').exists())
        self.assertTrue(self.models.is_dir())

    def test_startup_timeout_stops_only_owned_child(self):
        self.prepare()
        def spawn(command, **kwargs):
            process = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(30)'], **kwargs)
            self.processes.append(process); return process
        with self.assertRaisesRegex(ValueError, 'timed out'):
            with self.session(self.unused_endpoint(), popen=spawn,
                inspect=lambda *a, **k: {'service_status': 'unavailable'}, startup_timeout=0):
                self.fail('Must not report readiness')
        self.assertIsNotNone(self.processes[-1].poll())
        self.assertFalse((self.state / 'serve.lock').exists())

    def test_concurrent_runtime_target_is_preserved(self):
        def concurrent_download(*args):
            self.runtime.mkdir(); (self.runtime / 'keep').write_text('other owner')
            return SimpleNamespace(path=self.archive)
        with self.assertRaises(ValueError):
            runtime.prepare_runtime(self.runtime, self.cache, resources_path=self.resources,
                download=concurrent_download, probe=lambda *a: None)
        self.assertEqual((self.runtime / 'keep').read_text(), 'other owner')

    def test_in_progress_runtime_cannot_start(self):
        self.prepare(); (self.runtime / '.odessy-preparing').write_text('owner')
        manifest, resources = runtime.load_release(self.resources)
        with self.assertRaisesRegex(ValueError, 'not completed'):
            runtime.validate_installation(self.runtime, manifest, resources)

    def test_owned_child_stops_on_interrupt_and_version_conflict(self):
        self.prepare()
        for scenario in ('interrupt', 'wrong-version', 'early-exit'):
            class Process:
                returncode = 1 if scenario == 'early-exit' else None
                def poll(self): return self.returncode
                def terminate(self): self.returncode = -1
                def wait(self, timeout=None): return self.returncode
                def kill(self): self.returncode = -9
            process = Process()
            calls = [0]
            def inspect(*args, **kwargs):
                calls[0] += 1
                if calls[0] == 1: return {'service_status': 'unavailable'}
                return {'service_status': 'available', 'version': 'wrong' if scenario == 'wrong-version' else '0.40.6'}
            expected = KeyboardInterrupt if scenario == 'interrupt' else ValueError
            with self.subTest(scenario=scenario), self.assertRaises(expected):
                with self.session(self.unused_endpoint(), inspect=inspect, popen=lambda *a, **k: process,tree_factory=None):
                    raise KeyboardInterrupt()
            self.assertIsNotNone(process.poll())
            self.assertFalse((self.state / 'serve.lock').exists())

    def test_prefixless_zip_still_rejects_windows_unsafe_paths(self):
        for name in ('../outside', '/outside', 'C:/outside', 'lib/../bad', 'lib/NUL.txt', 'bad\\name', 'lib/file:ads'):
            with self.subTest(name=name), self.assertRaises(ArchiveError): relative_member(name, '')
        self.assertEqual(relative_member('lib/ollama/LICENSE', ''), Path('lib/ollama/LICENSE'))

    def test_default_preview_has_no_download_or_directory_creation(self):
        output = io.StringIO()
        manifest, resources = runtime.load_release(self.resources)
        with patch.object(runtime, 'load_release', return_value=(manifest, resources)), \
             patch.object(runtime, 'prepare_runtime') as prepare, \
             contextlib.redirect_stdout(output):
            code = runtime.main(['--runtime-dir', str(self.runtime), '--model-dir', str(self.models),
                                 '--state-dir', str(self.state), '--cache-dir', str(self.cache)])
        self.assertEqual(code, 0)
        prepare.assert_not_called()
        self.assertFalse(self.runtime.exists())
        self.assertFalse(self.models.exists())
        self.assertFalse(self.state.exists())
        self.assertFalse(self.cache.exists())


if __name__ == '__main__':
    unittest.main()
