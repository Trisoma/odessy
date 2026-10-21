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
        self.assertEqual(step.validate()['config_file'], str(self.root / 'config/odessy.env'))

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
