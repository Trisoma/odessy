"""Wizard threading, preview boundaries, cooperative cancellation and Tk integration."""
from __future__ import annotations
import dataclasses
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from installer import checkpoint, workflow
from installer.wizard_controller import Backend, Controller, Options, QueueLog


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'install'
        self.options = Options(str(self.root), str(Path(self.temp.name) / 'cache'), str(Path(self.temp.name) / 'bundle'))
        self.plan = dict(install_dir=str(self.root), cache_dir=self.options.cache_dir, tool_bundle=self.options.tool_bundle,
                        endpoint=self.options.endpoint, timeout=180, release_sha256='a'*64)
        self.build = patch.object(workflow, 'build_plan', return_value=self.plan).start()
        self.budget = patch.object(workflow, 'combined_budget', return_value=[{'sufficient': True}]).start()
        self.addCleanup(patch.stopall)

    def test_preview_has_no_installation_side_effects(self):
        with patch.object(workflow, 'execute') as execute:
            result = Backend().preview(self.options)
        self.assertTrue(result['sufficient']); self.assertFalse(self.root.exists()); execute.assert_not_called()
        self.assertEqual(result['options'], dataclasses.asdict(self.options))

    def test_foreign_directory_preserved(self):
        self.root.mkdir(); note = self.root / 'my-file'; note.write_text('preserve')
        with self.assertRaises(ValueError): Backend().preview(self.options)
        self.assertEqual(note.read_text(), 'preserve')

    def test_empty_and_invalid_timeout_rejected_before_build(self):
        for options in (dataclasses.replace(self.options, tool_bundle=' '), dataclasses.replace(self.options, timeout='bad')):
            with self.assertRaises(ValueError): Backend().preview(options)
        self.build.assert_not_called(); self.assertFalse(self.root.exists())

    def test_low_space_keeps_preview_but_disables_installation(self):
        self.budget.return_value = [{'sufficient': False}]
        self.assertFalse(Backend().preview(self.options)['sufficient'])
        self.assertFalse(self.root.exists())

    def make_record(self):
        checkpoint.run_steps(self.root, self.plan, [checkpoint.Step('fixture', lambda: {}, lambda: {})])

    def test_resume_requires_explicit_record_and_compatible_plan(self):
        self.make_record(); before = checkpoint.state_path(self.root).read_bytes()
        with self.assertRaises(ValueError): Backend().preview(self.options)
        options = dataclasses.replace(self.options, resume=True)
        self.assertTrue(Backend().preview(options)['resume'])
        self.plan['release_sha256'] = 'b'*64
        with self.assertRaises(ValueError): Backend().preview(options)
        self.assertEqual(checkpoint.state_path(self.root).read_bytes(), before)

    def test_missing_resume_record_rejected(self):
        with self.assertRaises(ValueError): Backend().preview(dataclasses.replace(self.options, resume=True))
        self.assertFalse(self.root.exists())

    def test_record_read_only_restores_original_options(self):
        self.make_record(); before = checkpoint.state_path(self.root).read_bytes()
        result = Backend().record(self.options)
        self.assertTrue(result['options']['resume']); self.assertEqual(result['options']['cache_dir'], self.options.cache_dir)
        self.assertEqual(checkpoint.state_path(self.root).read_bytes(), before)

    def test_execute_forwards_cancel_and_events(self):
        cancel = lambda: True; emit = lambda event: None
        with patch.object(workflow, 'execute', return_value={'status': 'cancelled'}) as execute:
            Backend().execute({'plan': self.plan, 'resume': True}, cancel, emit)
        self.assertIs(execute.call_args.kwargs['cancelled'], cancel)
        self.assertIs(execute.call_args.kwargs['emit'], emit)
        self.assertTrue(execute.call_args.kwargs['resume'])


class CancelTests(unittest.TestCase):
    def test_cancel_before_first_stage_records_without_preparing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'install'; prepared = []
            step = checkpoint.Step('fixture', lambda: prepared.append(True), lambda: None)
            with self.assertRaises(KeyboardInterrupt): checkpoint.run_steps(root, {}, [step], cancelled=lambda: True)
            record = checkpoint.read_state(root)
            self.assertEqual(record['status'], 'cancelled'); self.assertEqual(prepared, [])
            self.assertFalse(record['summary']['workflow_complete'])
            with checkpoint.writer_lock(root): pass

    def test_cancel_after_completed_stage_preserves_output_for_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'install'; token = threading.Event(); path = root / 'complete'
            def prepare():
                path.write_text('valid'); token.set(); return {}
            first = checkpoint.Step('first', prepare, lambda: {} if path.exists() else None)
            second = checkpoint.Step('second', lambda: {}, lambda: {})
            with self.assertRaises(KeyboardInterrupt): checkpoint.run_steps(root, {}, [first, second], cancelled=token.is_set)
            state = checkpoint.read_state(root)
            self.assertEqual([s['status'] for s in state['steps']], ['completed', 'cancelled'])
            self.assertEqual(path.read_text(), 'valid')
            token.clear()
            result = checkpoint.run_steps(root, {}, [first, second], resume=True, cancelled=token.is_set)
            self.assertEqual(result['steps'][0]['action'], 'reused'); self.assertEqual(result['status'], 'completed')

    def test_download_adapter_honors_cancel_before_fetch_and_during_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = dict(install_dir=folder, cache_dir=folder, tool_bundle=folder, endpoint='http://127.0.0.1:11435', model='fixture')
            token = threading.Event()
            def prepare(*args, download, **kwargs):
                return download(object(), Path(folder))
            def transfer(artifact, cache, *, progress):
                token.set(); progress('fixture', 1, 10)
            with patch.object(workflow.runtime, 'load_release', return_value=(None, None)), \
                 patch.object(workflow.runtime, 'prepare_runtime', side_effect=prepare), \
                 patch.object(workflow, 'download_artifact', side_effect=transfer) as download:
                step = workflow.steps_for(plan, cancelled=token.is_set)[1]
                token.set()
                with self.assertRaises(KeyboardInterrupt): step.prepare()
                download.assert_not_called()
                token.clear()
                with self.assertRaises(KeyboardInterrupt): step.prepare()
                self.assertEqual(download.call_count, 1)

    def test_workflow_passes_cancel_token_to_stage_runner(self):
        plan = dict(install_dir='D:/fixture', cache_dir='D:/cache', tool_bundle='D:/bundle', endpoint='http://127.0.0.1:11435', timeout=180)
        token = lambda: True
        with patch.object(workflow.ollama_runtime, 'require_windows'), patch.object(workflow, 'build_plan', return_value=plan), \
             patch.object(workflow, 'combined_budget', return_value=[]), patch.object(workflow, 'steps_for', return_value=[]) as steps, \
             patch.object(workflow, 'run_steps', return_value={}) as run:
            workflow.execute(plan, cancelled=token)
        self.assertIs(run.call_args.kwargs['cancelled'], token); self.assertIs(steps.call_args.kwargs['cancelled'], token)


class WorkerTests(unittest.TestCase):
    def collect(self, controller):
        controller.thread.join(3)
        self.assertFalse(controller.thread.is_alive())
        messages = []
        while not controller.messages.empty(): messages.append(controller.messages.get_nowait())
        return messages

    def test_thread_delivers_progress_logs_and_restores_streams(self):
        class Fake:
            def execute(self, value, cancelled, emit):
                print('download diagnostic'); emit({'step': 'runtime'})
                return {'status': 'completed'}
        original = sys.stdout, sys.stderr; controller = Controller(Fake()); controller.start('execute', {})
        messages = self.collect(controller)
        self.assertEqual((sys.stdout, sys.stderr), original)
        self.assertEqual(messages[-1]['kind'], 'finished')
        self.assertTrue(any(m['kind'] == 'log' and 'download diagnostic' in m['text'] for m in messages))
        self.assertTrue(any(m['kind'] == 'progress' for m in messages))

    def test_single_worker_and_cooperative_cancel(self):
        entered = threading.Event(); release = threading.Event()
        class Fake:
            def execute(self, value, cancelled, emit):
                entered.set(); release.wait(3)
                if cancelled(): raise KeyboardInterrupt()
                return {}
        controller = Controller(Fake()); controller.start('execute', {})
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(ValueError): controller.start('preview', None)
            self.assertTrue(controller.request_cancel())
        finally: release.set()
        self.assertEqual(self.collect(controller)[-1]['kind'], 'cancelled')

    def test_error_restores_streams_and_explains_timeout(self):
        class Fake:
            def preview(self, value): raise ValueError('call timed out')
        original = sys.stdout, sys.stderr; controller = Controller(Fake()); controller.start('preview', None)
        result = self.collect(controller)[-1]
        self.assertEqual(result['kind'], 'error'); self.assertNotEqual(result['message'], result['error'])
        self.assertEqual((sys.stdout, sys.stderr), original)

    def test_log_queue_is_bounded_without_blocking_worker(self):
        messages = queue.Queue(maxsize=2); log = QueueLog(messages)
        log.write('x'*10000 + '\n'); log.flush()
        self.assertEqual(messages.qsize(), 2)


class TkTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
            self.root = tk.Tk(); self.root.withdraw()
        except (ImportError, RuntimeError) as error: self.skipTest(str(error))
        except tk.TclError as error: self.skipTest(str(error))
        from installer.wizard import Wizard
        self.options = Options('D:/fixture-install', 'D:/fixture-cache', 'D:/fixture-bundle')
        self.release = threading.Event(); self.entered = threading.Event()
        owner = self
        class Fake:
            sufficient = True
            def preview(self, options):
                return dict(plan=dict(install_dir=options.install_dir, cache_dir=options.cache_dir, model='fixture-model'),
                            volumes=[], sufficient=self.sufficient, resume=options.resume, options=dataclasses.asdict(options),
                            options_sha256=checkpoint.fingerprint(dataclasses.asdict(options)))
            def execute(self, value, cancelled, emit):
                owner.entered.set(); owner.release.wait(3)
                if cancelled(): raise KeyboardInterrupt()
                emit(dict(step='runtime', status='completed', index=2, total=8, completed_steps=2))
                return dict(status='completed', summary={'workflow_complete': True, 'installation_ready': False})
        self.backend = Fake(); self.controller = Controller(self.backend)
        self.ui = Wizard(self.root, self.options, controller=self.controller)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.controller.request_cancel(); self.release.set()
        if self.controller.thread: self.controller.thread.join(3)
        try:
            for callback in self.root.tk.call('after', 'info'): self.root.after_cancel(callback)
            self.root.destroy()
        except Exception: pass

    def pump(self):
        deadline = time.monotonic() + 4
        while self.ui.active and time.monotonic() < deadline:
            self.root.update(); time.sleep(.01)
        self.assertFalse(self.ui.active)
        if self.controller.thread: self.controller.thread.join(1)

    def preview(self):
        self.ui.primary_action(); self.pump(); self.assertEqual(self.ui.page, 1)

    def test_preview_does_not_execute_and_low_space_blocks_start(self):
        self.backend.sufficient = False; self.preview()
        self.assertFalse(self.entered.is_set()); self.assertIn('disabled', self.ui.primary.state())
        self.ui.primary_action(); self.assertFalse(self.ui.active)

    def test_changed_settings_require_new_preview(self):
        self.preview(); self.ui.fields['timeout'].set('300'); self.ui.primary_action()
        self.assertEqual(self.ui.page, 0); self.assertFalse(self.entered.is_set())

    def test_success_updates_ui_after_background_worker(self):
        self.preview(); self.release.set(); self.ui.primary_action(); self.pump()
        self.assertEqual(self.ui.page, 2); self.assertEqual(self.ui.outcome, 'completed')
        self.assertEqual(float(self.ui.bar['value']), 8)

    def test_stop_keeps_window_and_offers_resume_after_worker_cleanup(self):
        self.preview(); self.ui.primary_action(); self.assertTrue(self.entered.wait(2))
        self.ui.stop(); self.root.update()
        self.assertFalse(self.ui.close_pending); self.assertTrue(self.ui.active)
        self.release.set(); self.pump()
        self.assertTrue(self.root.winfo_exists()); self.assertEqual(self.ui.outcome, 'cancelled')
        self.ui.primary_action(); self.pump()
        self.assertFalse(self.ui.resume); self.assertEqual(self.ui.page, 1)  # Fake backend created no checkpoint.

    def test_completed_historical_record_can_open_launcher_without_resuming_installation(self):
        from installer.wizard import STAGES
        options = dataclasses.asdict(self.options)
        state = {'status': 'completed', 'steps': [{'name': name, 'status': 'completed', 'error': None} for name in STAGES]}
        self.ui.handle({'kind': 'finished', 'job': 'record', 'result': {'state': state, 'options': options}})
        self.assertEqual(self.ui.outcome, 'completed')
        with patch('installer.wizard.subprocess.Popen') as spawn:
            self.ui.primary_action()
        self.assertIn('installer.launcher', spawn.call_args.args[0])
        self.assertEqual(Path(spawn.call_args.args[0][-1]), Path(self.options.install_dir))
        self.assertFalse(self.ui.active)

    def test_shortcuts_are_disabled_until_completion_and_use_selected_locations(self):
        self.assertIn('disabled',self.ui.shortcut_button.state())
        self.ui.outcome='completed';self.ui.installed_root=Path(self.options.install_dir);self.ui.sync_controls()
        self.assertNotIn('disabled',self.ui.shortcut_button.state())
        self.ui.shortcut_menu.set(False)
        with patch.object(sys,'frozen',True,create=True),patch.object(self.ui,'begin') as begin:
            self.ui.create_shortcuts()
        self.assertEqual(begin.call_args.args[0],'shortcuts')
        self.assertTrue(begin.call_args.args[1]['desktop']);self.assertFalse(begin.call_args.args[1]['start_menu'])

    def test_shortcut_failure_keeps_completed_installation_and_launch_action(self):
        self.ui.outcome='completed';self.ui.installed_root=Path(self.options.install_dir);self.ui.show(2)
        self.ui.handle(dict(kind='error',job='shortcuts',message='foreign shortcut',error='foreign shortcut'))
        self.assertEqual(self.ui.outcome,'completed');self.assertEqual(self.ui.page,2)
        self.assertIn('应用安装保留',self.ui.result_note.get())

    def test_window_close_waits_for_active_worker(self):
        self.preview(); self.ui.primary_action(); self.assertTrue(self.entered.wait(2))
        self.ui.close(); self.root.update()
        self.assertTrue(self.root.winfo_exists()); self.assertTrue(self.ui.close_pending)
        self.release.set(); self.pump()
        with self.assertRaises(Exception): self.root.winfo_exists()


if __name__ == '__main__': unittest.main()
