"""Checkpoint atomicity, verified retry and single-writer ownership tests."""
from __future__ import annotations
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from installer import checkpoint
from installer.checkpoint import Step, read_state, run_steps, state_path, writer_lock


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'installation'
        self.plan = {'release': 'fixture-release', 'timeout': 180}
        self.prepared = []; self.events = []

    def step(self, name, *, fail=None, always=False):
        path = self.root / (name + '.complete')
        def validate():
            if not path.exists(): return None
            if path.read_text() != 'valid': raise ValueError('corrupt component')
            return {'verified': name}
        def prepare():
            self.prepared.append(name)
            if fail: raise fail
            path.write_text('valid')
            return {'verified': name}
        return Step(name, prepare, validate, always_run=always)

    def test_success_records_stages_without_claiming_final_installer_ready(self):
        state = run_steps(self.root, self.plan, [self.step('one'), self.step('two')], emit=self.events.append)
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['summary'], {'workflow_complete': True, 'installation_ready': False})
        self.assertEqual(read_state(self.root), state)
        self.assertEqual([event['status'] for event in self.events], ['running', 'completed', 'running', 'completed'])
        self.assertEqual(self.prepared, ['one', 'two'])

    def test_failure_record_and_resume_revalidates_completed_component(self):
        with self.assertRaisesRegex(ValueError, 'fixture failure'):
            run_steps(self.root, self.plan, [self.step('one'), self.step('two', fail=ValueError('fixture failure'))])
        failed = read_state(self.root)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['steps'][1]['error'], 'fixture failure')
        self.prepared.clear()
        resumed = run_steps(self.root, self.plan, [self.step('one'), self.step('two')], resume=True, emit=self.events.append)
        self.assertEqual(self.prepared, ['two'])
        self.assertEqual(resumed['steps'][0]['action'], 'reused')
        self.assertEqual(resumed['steps'][1]['attempts'], 2)
        self.assertTrue(any(event['status'] == 'failed' for event in resumed['history']))
        self.assertEqual(self.events[0]['completed_steps'], 0)

    def test_cancel_records_and_releases_writer_lock(self):
        with self.assertRaises(KeyboardInterrupt):
            run_steps(self.root, self.plan, [self.step('one', fail=KeyboardInterrupt())])
        self.assertEqual(read_state(self.root)['status'], 'cancelled')
        with writer_lock(self.root): pass
        self.assertEqual(run_steps(self.root, self.plan, [self.step('one')], resume=True)['status'], 'completed')

    def test_completed_flag_never_skips_corrupted_actual_output(self):
        run_steps(self.root, self.plan, [self.step('one')])
        target = self.root / 'one.complete'; target.write_text('corrupt')
        self.prepared.clear()
        with self.assertRaisesRegex(ValueError, 'corrupt'):
            run_steps(self.root, self.plan, [self.step('one')], resume=True)
        self.assertEqual(target.read_text(), 'corrupt'); self.assertEqual(self.prepared, [])
        self.assertFalse(read_state(self.root)['summary']['workflow_complete'])

    def test_missing_output_from_running_checkpoint_is_recovered(self):
        with self.assertRaises(ValueError):
            run_steps(self.root, self.plan, [self.step('one', fail=ValueError('interrupted'))])
        data = read_state(self.root); data['status'] = 'running'; data['steps'][0]['status'] = 'running'
        checkpoint.save_state(self.root, data)
        self.assertEqual(run_steps(self.root, self.plan, [self.step('one')], resume=True)['status'], 'completed')

    def test_valid_output_before_checkpoint_commit_is_reused(self):
        with self.assertRaises(ValueError):
            run_steps(self.root, self.plan, [self.step('one', fail=ValueError('fixture'))])
        (self.root / 'one.complete').write_text('valid')
        self.prepared.clear()
        state = run_steps(self.root, self.plan, [self.step('one')], resume=True)
        self.assertEqual(self.prepared, []); self.assertEqual(state['steps'][0]['action'], 'reused')

    def test_release_or_paths_changed_preserves_old_state(self):
        run_steps(self.root, self.plan, [self.step('one')])
        old = state_path(self.root).read_bytes()
        with self.assertRaisesRegex(ValueError, 'changed'):
            run_steps(self.root, {**self.plan, 'release': 'another'}, [self.step('one')], resume=True)
        self.assertEqual(state_path(self.root).read_bytes(), old)

    def test_only_explicit_mutable_timeout_can_change(self):
        run_steps(self.root, self.plan, [self.step('one')])
        state = run_steps(self.root, {**self.plan, 'timeout': 300}, [self.step('one')], resume=True, mutable_options=('timeout',))
        self.assertEqual(state['plan']['timeout'], 300)

    def test_connection_style_step_always_runs_again(self):
        steps = [self.step('normal'), self.step('connection', always=True)]
        run_steps(self.root, self.plan, steps); self.prepared.clear()
        run_steps(self.root, self.plan, steps, resume=True)
        self.assertEqual(self.prepared, ['connection'])

    def test_always_run_stage_cannot_pass_without_result_record(self):
        step = Step('connection', lambda: None, lambda: None, always_run=True)
        with self.assertRaisesRegex(ValueError, 'result record'):
            run_steps(self.root, self.plan, [step])
        self.assertEqual(read_state(self.root)['status'], 'failed')

    def test_existing_foreign_directory_is_not_adopted(self):
        self.root.mkdir(); target = self.root / 'keep'; target.write_text('owner')
        with self.assertRaises(ValueError): run_steps(self.root, self.plan, [self.step('one')])
        self.assertEqual(target.read_text(), 'owner'); self.assertFalse((self.root / '.installer').exists())

    def test_new_run_never_implicitly_resumes(self):
        run_steps(self.root, self.plan, [self.step('one')])
        old = state_path(self.root).read_bytes()
        with self.assertRaises(ValueError): run_steps(self.root, self.plan, [self.step('one')])
        self.assertEqual(state_path(self.root).read_bytes(), old)

    def test_failed_atomic_write_preserves_previous_json(self):
        run_steps(self.root, self.plan, [self.step('one')]); old = state_path(self.root).read_bytes()
        data = read_state(self.root)
        with patch.object(checkpoint.os, 'replace', side_effect=OSError('fixture disk failure')), self.assertRaises(OSError):
            checkpoint.save_state(self.root, data)
        self.assertEqual(state_path(self.root).read_bytes(), old)
        self.assertFalse(list((self.root / '.installer').glob('.state-*.tmp')))

    def test_corrupt_plan_or_completion_flags_are_not_accepted(self):
        run_steps(self.root, self.plan, [self.step('one')]); original = read_state(self.root)
        for key, value in (('plan_sha256', '0' * 64), ('status', []), ('summary', {'workflow_complete': False, 'installation_ready': False})):
            state = dict(original); state[key] = value
            state_path(self.root).write_text(json.dumps(state))
            with self.assertRaises(ValueError): read_state(self.root)

    def test_parallel_writer_refused_and_kernel_lock_released_after_exit(self):
        self.root.mkdir(); (self.root / '.installer').mkdir()
        code = ('from installer.checkpoint import writer_lock; from pathlib import Path; import time; '
                f'lock=writer_lock(Path({str(self.root)!r})); lock.__enter__(); print("LOCKED",flush=True); time.sleep(30)')
        process = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'LOCKED')
            with self.assertRaisesRegex(ValueError, 'Another installation'):
                with writer_lock(self.root): pass
        finally:
            if process.poll() is None: process.kill()
            process.communicate(timeout=5)
        with writer_lock(self.root): pass


if __name__ == '__main__':
    unittest.main()
