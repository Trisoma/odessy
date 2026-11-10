"""Role-aware planning must keep the whole installation, including Harness.

These tests pin the regression that a positional step slice caused: adding
``harness_runtime`` before ``dependencies`` pushed ``application`` out of the selected
stages, so a role-aware install never assembled the application.
"""
import pytest

from installer import role_aware
from installer.core.checkpoint import Step


def _steps(names):
    return [Step(name, lambda: {'ok': True}, lambda: {'ok': True}) for name in names]


def test_role_steps_include_harness_and_application_in_order(monkeypatch):
    monkeypatch.setattr(role_aware.workflow, 'steps_for',
                        lambda *args, **kwargs: _steps(role_aware.workflow.STEP_NAMES))
    plan = {'install_dir': 'C:/install', 'selection': {'role': 'execution'}}
    names = [step.name for step in role_aware.steps_for(plan)]
    assert names == ['tool_bundle', 'runtime', 'harness_runtime', 'dependencies',
                     'application', 'configuration']
    assert 'application' in names
    assert names.index('harness_runtime') < names.index('dependencies')


def test_a_workflow_missing_a_required_stage_is_refused(monkeypatch):
    names = [name for name in role_aware.workflow.STEP_NAMES if name != 'application']
    monkeypatch.setattr(role_aware.workflow, 'steps_for',
                        lambda *args, **kwargs: _steps(names))
    plan = {'install_dir': 'C:/install', 'selection': {'role': 'execution'}}
    with pytest.raises(ValueError, match='application'):
        role_aware.steps_for(plan)


class _Artifact:
    def __init__(self, name, size, sha='0' * 64):
        self.id, self.size_bytes, self.sha256 = name, size, sha
        self.url = f'https://example.invalid/{name}'
        self.version = '0.2.1rc2'


class _Manifest:
    def __init__(self, download, artifacts):
        self.download_bytes, self.artifacts = download, tuple(artifacts)


def test_role_plan_counts_the_harness_wheel_and_payload(monkeypatch):
    wheel = _Artifact('harness-runtime', 83_000_000)
    monkeypatch.setattr(role_aware.workflow, 'build_plan',
                        lambda *args, **kwargs: {'install_dir': 'C:/install'})
    monkeypatch.setattr(role_aware.runtime, 'load_release',
                        lambda *args: (_Manifest(100, [_Artifact('python', 100)]),
                                       {'runtimes': {'python': {'unpacked_bytes': 1_000}}}))
    monkeypatch.setattr(role_aware, 'load_resources',
                        lambda *args: (_Manifest(50, [_Artifact('dep', 50)]),
                                       {'layout': {'dep': {'unpacked_bytes': 500}}}))
    monkeypatch.setattr(role_aware.harness, 'load_declaration', lambda: {'runtime': {}})
    monkeypatch.setattr(role_aware.harness, 'wheel_artifact', lambda declaration: wheel)
    monkeypatch.setattr(role_aware.harness, 'unpacked_bytes', lambda declaration: 273_000_000)
    monkeypatch.setattr(role_aware.application, 'validate_bundle',
                        lambda bundle: ({}, {'sha256': 'a' * 64, 'unpacked_bytes': 10}))
    monkeypatch.setattr(role_aware.application, 'source_snapshot', lambda repo: {'src': b'x'})

    from odessy.setup.contracts import SetupSelection
    selection = SetupSelection.from_dict({'role': 'execution', 'platform': 'windows',
                                          'architecture': 'x86_64'})
    plan = role_aware.build_plan('C:/install', 'C:/cache', 'C:/bundle', selection)

    budget = plan['budget_bytes']
    assert budget['cache'] == 100 + 2 * 50 + wheel.size_bytes
    # Harness payload is counted in the application figure (twice, for the rollback copy).
    assert budget['application'] == 2 * (1_000 + 500 + 10 + len(b'x') + 273_000_000)
    assert budget['models'] == 0
    ids = [item['id'] for item in plan['downloads']]
    assert ids.count('harness-runtime') == 1
