"""Installed-model connector checks using isolated synthetic responses."""
from __future__ import annotations
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from installer import connection
from installer.session_process import run_application


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.app = self.root / 'app'
        runtime = self.root / 'runtime'; dependencies = self.root / 'deps'
        for path in (self.app / 'src/odessy/__main__.py', self.app / 'launch.py',
                     self.app / 'tools/dist/src/server.js', runtime / 'node-runtime/node.exe',
                     dependencies / 'venv/Scripts/python.exe'):
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')
        self.settings = dict(schema_version=1, status='ready', runtime_dir=str(runtime), dependency_dir=str(dependencies),
            python=str(dependencies / 'venv/Scripts/python.exe'), runtime_versions={}, tool_sha256='a' * 64,
            source_sha256={'src/odessy/__main__.py': hashlib.sha256(b'fixture').hexdigest()})
        (self.app / 'odessy-application.json').write_text(json.dumps(self.settings), encoding='utf-8')
        self.model = {'model': 'fixture:1b', 'manifest_sha256': 'b' * 64}
        self.calls = []; self.workspaces = []
        self.print_patch = patch('builtins.print'); self.print_patch.start(); self.addCleanup(self.print_patch.stop)

    def runner(self, command, **kwargs):
        self.calls.append((command, kwargs)); self.workspaces.append(kwargs['cwd'])
        if len(self.calls) == 1:
            token = re.search(r'ODESSY_OK_[0-9a-f]+', command[-1]).group()
            return '[odessy:model-repair] parsed final response:\n' + token + '\n' + token + '\n'
        filename = next(kwargs['cwd'].glob('odessy-check-*.txt')).name
        self.assertNotIn(filename, command[-1])
        return '[odessy:model-repair] parsed tool request:\n{"name": "filesystem.list", "arguments": {"path": "."}}\n' + filename

    def verify(self, **kwargs):
        return connection.verify_connection(self.app, self.root / 'models', 'http://127.0.0.1:11434',
            verify_files=lambda path: self.model, check_service=kwargs.pop('check_service', lambda *a: None),
            run=kwargs.pop('run', self.runner), **kwargs)

    def test_two_real_backend_commands_and_unknown_fixture_evidence(self):
        report = self.verify()
        self.assertTrue(report['inference_verified']); self.assertTrue(report['tool_round_trip_verified'])
        self.assertFalse(report['installation_ready'])
        self.assertEqual(len(self.calls), 2)
        for command, kwargs in self.calls:
            self.assertEqual(command[0], self.settings['python'])
            self.assertIn('-I', command); self.assertIn('-B', command)
            self.assertEqual(command[-3:-1], ['--model-backend', 'lfm'])
            self.assertNotIn('--fake-model', command)
            self.assertNotEqual(kwargs['cwd'], self.app)
        self.assertTrue(all(not path.exists() for path in self.workspaces))

    def test_cancel_after_first_model_call_does_not_start_second_call(self):
        stopped = False
        def run(*args, **kwargs):
            nonlocal stopped
            result = self.runner(*args, **kwargs)
            stopped = True
            return result
        with self.assertRaises(KeyboardInterrupt):
            self.verify(run=run, cancelled=lambda: stopped)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(all(not path.exists() for path in self.workspaces))

    def test_cancel_before_verification_does_not_probe_files_or_service(self):
        with patch.object(connection, 'inspect_application') as inspect:
            with self.assertRaises(KeyboardInterrupt):
                self.verify(cancelled=lambda: True, inspect=inspect)
        inspect.assert_not_called()

    def test_smoke_environment_preserves_parent_but_not_user_routing(self):
        with patch.dict(os.environ, {'ODESSY_MODEL_BACKEND': 'openai', 'ODESSY_TOOL_SERVER_URL': 'https://example.com',
                                    'HTTP_PROXY': 'http://example.com', 'NODE_OPTIONS': '--require bad'}):
            self.verify()
            self.assertEqual(os.environ['ODESSY_MODEL_BACKEND'], 'openai')
            env = self.calls[0][1]['env']
            self.assertEqual(env['ODESSY_MODEL_BACKEND'], 'lfm')
            self.assertNotIn('ODESSY_TOOL_SERVER_URL', env); self.assertNotIn('HTTP_PROXY', env)
            self.assertNotIn('NODE_OPTIONS', env)
            self.assertEqual(env['NO_PROXY'], '*')
            self.assertEqual(env['ODESSY_MAX_MODEL_CALLS'], '3')
            self.assertTrue(Path(env['ODESSY_CONFIG_FILE']).is_absolute())

    def test_installation_check_matches_normal_session_generation_budget(self):
        from installer import start
        scratch=self.root/'session'
        info={'paths':{'runtime':Path(self.settings['runtime_dir']),'config':scratch/'profile.env'}}
        with patch.dict(os.environ,{'ODESSY_MODEL_NUM_PREDICT':'999999'}):
            self.verify()
            normal=start.session_environment(info,scratch)
        for _,kwargs in self.calls:
            self.assertEqual(kwargs['env']['ODESSY_MODEL_NUM_PREDICT'],normal['ODESSY_MODEL_NUM_PREDICT'])
            self.assertEqual(kwargs['env']['ODESSY_MODEL_NUM_PREDICT'],'2048')

    def test_changed_application_source_rejected_before_model_calls(self):
        (self.app / 'src/odessy/__main__.py').write_text('changed')
        with self.assertRaises(ValueError): self.verify()
        self.assertEqual(self.calls, [])

    def test_service_digest_failure_stops_before_inference(self):
        def failure(*args): raise ValueError('wrong digest')
        with self.assertRaisesRegex(ValueError, 'wrong digest'): self.verify(check_service=failure)
        self.assertEqual(self.calls, [])

    def test_service_digest_change_during_smoke_is_rejected(self):
        calls = [0]
        def check(*args):
            calls[0] += 1
            if calls[0] == 2: raise ValueError('service digest changed')
        with self.assertRaisesRegex(ValueError, 'digest changed'):
            self.verify(check_service=check)
        self.assertEqual(len(self.calls), 2)

    def test_echoed_prompt_is_not_a_final_model_response(self):
        with self.assertRaisesRegex(ValueError, 'final response marker'):
            self.verify(run=lambda command, **kwargs: command[-1])

    def test_wrong_tool_or_missing_random_filename_is_not_a_pass(self):
        for bad_output in ('{"name": "filesystem.list"}', 'imagined-name.txt'):
            self.calls.clear()
            def run(command, **kwargs):
                return self.runner(command, **kwargs) if not self.calls else bad_output
            with self.assertRaisesRegex(ValueError, 'undisclosed fixture'):
                self.verify(run=run)
            self.assertTrue(all(not path.exists() for path in self.workspaces))

    def test_unexpected_workspace_write_is_rejected(self):
        def run(command, **kwargs):
            output = self.runner(command, **kwargs)
            if len(self.calls) == 2: (kwargs['cwd'] / 'unexpected').write_text('change')
            return output
        with self.assertRaisesRegex(ValueError, 'workspace changed'): self.verify(run=run)
        self.assertTrue(all(not path.exists() for path in self.workspaces))

    def test_timeout_or_cancel_cleans_temporary_workspace(self):
        for error in (ValueError('fixture timeout'), KeyboardInterrupt()):
            seen = []
            def fail(command, **kwargs): seen.append(kwargs['cwd']); raise error
            with self.assertRaises(type(error)): self.verify(run=fail)
            self.assertTrue(all(not path.exists() for path in seen))

    def test_timeout_validation(self):
        for timeout in (0, 14, 601, float('nan')):
            with self.assertRaises(ValueError): self.verify(timeout=timeout)

    def test_preview_is_read_only(self):
        with patch.object(connection, 'verify_connection') as verify:
            self.assertEqual(connection.main(['--app-dir', str(self.app), '--model-dir', str(self.root / 'absent')]), 0)
        verify.assert_not_called(); self.assertFalse((self.root / 'absent').exists())

    def test_profile_written_only_after_success(self):
        profile = self.root / 'new-profile.env'
        report = dict(self.model, inference_verified=True)
        with patch.object(connection, 'verify_connection', return_value=report):
            self.assertEqual(connection.main(['--app-dir', str(self.app), '--run', '--write-config', '--config-file', str(profile)]), 0)
        self.assertIn('ODESSY_MODEL_BACKEND=lfm', profile.read_text())
        original = profile.read_bytes()
        with patch.object(connection, 'verify_connection') as verify:
            self.assertEqual(connection.main(['--app-dir', str(self.app), '--run', '--write-config', '--config-file', str(profile)]), 1)
        verify.assert_not_called(); self.assertEqual(profile.read_bytes(), original)

    def test_failed_smoke_does_not_create_profile(self):
        profile = self.root / 'absent' / 'profile.env'
        with patch.object(connection, 'verify_connection', side_effect=ValueError('no response')):
            self.assertEqual(connection.main(['--app-dir', str(self.app), '--run', '--write-config', '--config-file', str(profile)]), 1)
        self.assertFalse(profile.parent.exists())


class SessionProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.cleanup)
        self.root = Path(self.temp.name)

    def cleanup(self):
        # Windows can briefly keep a terminated process's working directory
        # busy after exit is reported. Live-child assertions remain mandatory;
        # only fixture removal retries, and persistent locks still fail.
        for attempt in range(7):
            try:
                self.temp.cleanup();return
            except OSError as error:
                if getattr(error,'winerror',None) not in {5,32,33} or attempt==6:raise
                time.sleep(.05*2**attempt)

    def execute(self, code, timeout=3):
        return run_application([sys.executable, '-I', '-c', code], cwd=self.root,
                               env=dict(os.environ), label='fixture process', timeout=timeout)

    def test_success_and_no_approval_stdin(self):
        self.assertEqual(self.execute('import sys; print("EOF="+repr(sys.stdin.read()))').strip(), "EOF=''")

    def test_failure_is_reported(self):
        with self.assertRaisesRegex(ValueError, 'fixture failure'):
            self.execute('import sys; sys.stderr.write("fixture failure"); sys.exit(3)')

    def test_timeout_kills_owned_parent_and_descendant(self):
        pid_file = self.root / 'child-pid'
        code = ('import subprocess,sys,time; from pathlib import Path; '
                'child=subprocess.Popen([sys.executable,"-I","-c","import time; time.sleep(30)"]); '
                f'Path({str(pid_file)!r}).write_text(str(child.pid)); time.sleep(30)')
        with self.assertRaisesRegex(ValueError, 'exceeded'):
            self.execute(code, timeout=1)
        self.assertTrue(pid_file.is_file())
        pid = int(pid_file.read_text())
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if handle:
                try:
                    code = wintypes.DWORD(); self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                    self.assertNotEqual(code.value, 259, 'Child process survived its owned job')
                finally:
                    kernel.CloseHandle(handle)

    @unittest.skipUnless(os.name=='nt','Windows process handle lifetime')
    def test_timeout_releases_native_handle_even_when_the_exception_is_retained(self):
        from installer import session_process
        popen=subprocess.Popen;processes=[]
        def spawn(*args,**kwargs):
            process=popen(*args,**kwargs);processes.append(process);return process
        with patch.object(session_process.subprocess,'Popen',side_effect=spawn):
            with self.assertRaisesRegex(ValueError,'exceeded') as caught:
                self.execute('import time; time.sleep(30)',timeout=.2)
        self.assertIsNotNone(caught.exception.__cause__)
        self.assertIsNotNone(processes[0].returncode)
        self.assertTrue(processes[0]._handle.closed)


if __name__ == '__main__':
    unittest.main()
