"""Workflow planning and stage wiring without downloads or model inference."""
from __future__ import annotations
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from installer import workflow
from installer.space import Volume


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'install'; self.cache = Path(self.temp.name) / 'cache'
        self.bundle = Path(self.temp.name) / 'bundle'; self.bundle.mkdir()
        self.print_patch = patch('builtins.print'); self.print_patch.start(); self.addCleanup(self.print_patch.stop)

    def plan(self, **kwargs):
        with patch.object(workflow, 'release_inputs', return_value=('a' * 64, 'fixture:1b', {'cache': 100, 'application': 200, 'models': 300})):
            return workflow.build_plan(self.root, self.cache, self.bundle, kwargs.get('endpoint', 'http://127.0.0.1:11435'), kwargs.get('timeout', 180))

    def test_combined_budget_groups_actual_shared_volumes(self):
        plan = self.plan()
        groups = workflow.combined_budget(plan, probe=lambda path: Volume('same-volume', 10**10))
        self.assertEqual(len(groups), 1); self.assertEqual(groups[0]['payload_bytes'], 600)
        self.assertEqual(groups[0]['contributions'], {'cache': 100, 'application': 200, 'models': 300})
        self.assertTrue(groups[0]['sufficient'])
        groups = workflow.combined_budget(plan, probe=lambda path: Volume('same-volume', 1))
        self.assertFalse(groups[0]['sufficient'])

    def test_paths_and_unsafe_endpoint_fail_before_changes(self):
        with self.assertRaises(ValueError): self.plan(endpoint='http://example.com')
        with self.assertRaises(ValueError): self.plan(timeout=0)
        with self.assertRaises(ValueError):
            workflow.build_plan(self.root, self.root / 'cache', self.bundle, 'http://127.0.0.1:11435', 180)
        self.assertFalse(self.root.exists())

    def test_installation_under_project_sources_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'project source'):
            workflow.build_plan(workflow.REPO_ROOT / 'installer' / 'generated-install', self.cache,
                                self.bundle, 'http://127.0.0.1:11435', 180)

    def test_status_does_not_validate_release_or_start_components(self):
        state = dict(plan={'install_dir': str(self.root)}, status='failed',
            summary={'workflow_complete': False, 'installation_ready': False},
            steps=[dict(name='runtime', status='failed', attempts=1, action='prepared', error='fixture failure',
                        result={'private_diagnostic': 'not needed in compact status'})])
        with patch.object(workflow, 'read_state', return_value=state), \
             patch.object(workflow, 'build_plan') as build, patch.object(workflow, 'execute') as execute:
            self.assertEqual(workflow.main(['--install-dir', str(self.root), '--status']), 0)
        build.assert_not_called(); execute.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_step_order_and_real_check_not_skipped(self):
        plan = self.plan(); steps = workflow.steps_for(plan)
        self.assertEqual(tuple(step.name for step in steps), workflow.STEP_NAMES)
        self.assertTrue(steps[6].always_run)
        self.assertTrue(all(not step.always_run for step in steps[:6]))

    def test_foreign_profile_is_preserved(self):
        plan = self.plan(); steps = workflow.steps_for(plan)
        profile = workflow.paths_for(self.root)['config']; profile.parent.mkdir(parents=True)
        profile.write_text('ODESSY_MODEL_BACKEND=openai\n')
        with self.assertRaises(ValueError): steps[-1].validate()
        self.assertEqual(profile.read_text(), 'ODESSY_MODEL_BACKEND=openai\n')

    def test_configuration_round_trip_matches_plan(self):
        plan = self.plan(); step = workflow.steps_for(plan)[-1]
        self.assertIsNone(step.validate()); step.prepare()
        self.assertEqual(step.validate()['config_file'], str((self.root / 'config/odessy.env').resolve()))

    def test_model_partial_download_can_continue_but_corruption_is_not_ignored(self):
        plan = self.plan(); step = workflow.steps_for(plan)[5]
        with patch.object(workflow.model, 'load_release', return_value=(None, {'model': 'fixture:1b'}, None)):
            self.assertIsNone(step.validate())
            manifest = workflow.model.manifest_path(self.root / 'models', 'fixture:1b')
            manifest.parent.mkdir(parents=True); manifest.write_text('bad')
            with patch.object(workflow.model, 'verify_model', side_effect=ValueError('corrupt')), self.assertRaises(ValueError): step.validate()

    def test_connection_context_closes_owned_service_before_success(self):
        plan = self.plan(); events = []
        @contextlib.contextmanager
        def service(*args):
            events.append('started'); yield {'reused': False}; events.append('stopped')
        with patch.object(workflow.ollama_runtime, 'managed_service', service), \
             patch.object(workflow.connection, 'verify_connection', return_value={'inference_verified': True}):
            report = workflow.steps_for(plan)[6].prepare()
        self.assertEqual(events, ['started', 'stopped']); self.assertTrue(report['owned_service_stopped'])

    def test_connection_failure_still_exits_service_context(self):
        plan = self.plan(); events = []
        @contextlib.contextmanager
        def service(*args):
            try: yield {'reused': False}
            finally: events.append('stopped')
        with patch.object(workflow.ollama_runtime, 'managed_service', service), \
             patch.object(workflow.connection, 'verify_connection', side_effect=ValueError('inference failed')):
            with self.assertRaises(ValueError): workflow.steps_for(plan)[6].prepare()
        self.assertEqual(events, ['stopped'])

    def test_connection_retry_preserves_abandoned_locks_and_uses_fresh_session(self):
        plan = self.plan(); paths = workflow.paths_for(self.root)
        paths['service_state'].mkdir(parents=True)
        legacy = paths['service_state'] / 'serve.lock'; legacy.write_text('old installer')
        sessions = []

        @contextlib.contextmanager
        def service(runtime, models, state, *args):
            sessions.append(state)
            state.mkdir(parents=True, exist_ok=True)
            lock = state / 'serve.lock'
            if lock.exists(): raise ValueError('State session lock already exists')
            lock.write_text('owned session')
            # Simulate abrupt interruption: the first service lock survives.
            try: yield {'reused': False}
            finally:
                if len(sessions) > 1: lock.unlink()

        with patch.object(workflow.ollama_runtime, 'managed_service', service), \
             patch.object(workflow.connection, 'verify_connection', side_effect=[
                 ValueError('interrupted verification'), {'inference_verified': True}]):
            step = workflow.steps_for(plan)[6]
            with self.assertRaisesRegex(ValueError, 'interrupted verification'): step.prepare()
            report = step.prepare()
        self.assertTrue(report['owned_service_stopped'])
        self.assertNotEqual(sessions[0], sessions[1])
        self.assertTrue(all(paths['service_state'] in path.parents for path in sessions))
        self.assertEqual(legacy.read_text(), 'old installer')
        self.assertEqual((sessions[0] / 'serve.lock').read_text(), 'owned session')

    def test_cancel_before_connection_never_starts_a_service(self):
        plan = self.plan()
        with patch.object(workflow.ollama_runtime, 'managed_service') as service:
            with self.assertRaises(KeyboardInterrupt):
                workflow.steps_for(plan, cancelled=lambda: True)[6].prepare()
        service.assert_not_called()

    def test_interrupted_checkpoint_resumes_to_complete_without_reinstalling_components(self):
        from installer import checkpoint
        plan = self.plan(); state_dirs = []; prepared = []
        component = self.root / 'component.complete'
        def prepare():
            prepared.append('component'); component.write_text('verified component')
            return {'verified': True}
        def validate():
            return {'verified': True} if component.exists() else None

        @contextlib.contextmanager
        def service(runtime, models, state, *args):
            state.mkdir(parents=True, exist_ok=True)
            lock = state / 'serve.lock'
            with lock.open('x') as stream: stream.write('owned session')
            state_dirs.append(state)
            try: yield {'reused': False}
            finally:
                if len(state_dirs) > 1: lock.unlink()

        with patch.object(workflow.ollama_runtime, 'managed_service', service), \
             patch.object(workflow.connection, 'verify_connection', side_effect=[
                 ValueError('interrupted verification'), {'inference_verified': True}]):
            steps = [checkpoint.Step('component', prepare, validate), *workflow.steps_for(plan)[6:]]
            with self.assertRaisesRegex(ValueError, 'interrupted verification'):
                checkpoint.run_steps(self.root, plan, steps)
            state = checkpoint.read_state(self.root)
            # Model the on-disk state left by a hard exit before failure is saved.
            state['status'] = state['steps'][1]['status'] = 'running'
            checkpoint.save_state(self.root, state)
            result = checkpoint.run_steps(self.root, plan, steps, resume=True)
        self.assertTrue(result['summary']['workflow_complete'])
        self.assertEqual(prepared, ['component'])
        self.assertEqual(result['steps'][0]['action'], 'reused')
        self.assertEqual(result['steps'][1]['attempts'], 2)
        self.assertTrue((state_dirs[0] / 'serve.lock').is_file())
        self.assertNotEqual(state_dirs[0], state_dirs[1])
        self.assertTrue((self.root / 'config/odessy.env').is_file())

    def test_cancel_during_service_startup_closes_it_without_model_checks(self):
        plan = self.plan(); stopped = False; events = []
        @contextlib.contextmanager
        def service(*args):
            nonlocal stopped
            stopped = True
            try: yield {'reused': False}
            finally: events.append('owned service stopped')
        with patch.object(workflow.ollama_runtime, 'managed_service', service), \
             patch.object(workflow.connection, 'verify_connection') as verify:
            with self.assertRaises(KeyboardInterrupt):
                workflow.steps_for(plan, cancelled=lambda: stopped)[6].prepare()
        verify.assert_not_called()
        self.assertEqual(events, ['owned service stopped'])

    def test_cancelled_verification_does_not_save_success_or_write_configuration(self):
        from installer import checkpoint
        plan = self.plan(); events = []
        @contextlib.contextmanager
        def service(*args):
            try: yield {'reused': False}
            finally: events.append('owned service stopped')
        with patch.object(workflow.ollama_runtime, 'managed_service', service), \
             patch.object(workflow.connection, 'verify_connection', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                checkpoint.run_steps(self.root, plan, workflow.steps_for(plan)[6:])
        state = checkpoint.read_state(self.root)
        self.assertEqual(state['status'], 'cancelled')
        self.assertFalse(state['summary']['workflow_complete'])
        self.assertFalse((self.root / 'config/odessy.env').exists())
        self.assertEqual(events, ['owned service stopped'])

    def test_incomplete_component_marker_is_preserved(self):
        target = self.root / 'runtime'; target.mkdir(parents=True)
        marker = target / '.odessy-preparing'; marker.write_text('old interrupted owner')
        with self.assertRaises(ValueError): workflow.existing_directory(target)
        self.assertEqual(marker.read_text(), 'old interrupted owner')

    def test_execute_rejects_changed_release_before_state_creation(self):
        plan = self.plan()
        with patch.object(workflow.ollama_runtime, 'require_windows'), \
             patch.object(workflow, 'build_plan', return_value={**plan, 'release_sha256': 'changed'}), \
             patch.object(workflow, 'run_steps') as run:
            with self.assertRaisesRegex(ValueError, 'changed after preview'): workflow.execute(plan)
        run.assert_not_called(); self.assertFalse(self.root.exists())

    def test_failed_combined_budget_never_starts_checkpoint(self):
        plan = self.plan()
        with patch.object(workflow.ollama_runtime, 'require_windows'), \
             patch.object(workflow, 'build_plan', return_value=plan), \
             patch.object(workflow, 'combined_budget', return_value=[{'sufficient': False}]), \
             patch.object(workflow, 'run_steps') as run:
            with self.assertRaisesRegex(ValueError, 'budget'): workflow.execute(plan)
        run.assert_not_called(); self.assertFalse(self.root.exists())

    def test_resume_restores_original_options_and_allows_explicit_timeout(self):
        plan = self.plan()
        with patch.object(workflow, 'read_state', return_value={'plan': plan}), \
             patch.object(workflow, 'build_plan', return_value=plan) as build, \
             patch.object(workflow, 'execute', return_value={'summary': {'workflow_complete': True}}) as execute:
            self.assertEqual(workflow.main(['--install-dir', str(self.root), '--resume', '--timeout', '300']), 0)
        self.assertEqual(build.call_args.args[2], self.bundle.resolve())
        self.assertEqual(build.call_args.args[3], plan['endpoint'])
        self.assertEqual(build.call_args.args[4], 300)
        self.assertTrue(execute.call_args.kwargs['resume'])

    def test_preview_does_not_run_workflow_or_create_state(self):
        plan = self.plan()
        with patch.object(workflow, 'build_plan', return_value=plan), \
             patch.object(workflow, 'execute') as execute:
            self.assertEqual(workflow.main(['--install-dir', str(self.root), '--tool-bundle', str(self.bundle)]), 0)
        execute.assert_not_called(); self.assertFalse(self.root.exists())


if __name__ == '__main__':
    unittest.main()
