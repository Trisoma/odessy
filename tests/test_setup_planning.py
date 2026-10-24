import pytest

from odessy.setup.contracts import SetupSelection
from odessy.setup.planning import build_plan


@pytest.mark.parametrize('role', ['coordinator', 'execution', 'combined'])
def test_headless_roles_never_force_model_downloads(role):
    plan = build_plan(SetupSelection(role=role))
    assert plan.downloads == ()
    assert 'flutter' not in plan.components
    assert ('node' in plan.components) == (role != 'coordinator')
    assert ('odessy-link' in plan.components) == (role != 'combined')
    assert plan.readiness != 'ready'


def test_existing_model_generates_selected_runtime_config():
    selection = SetupSelection.from_dict({'role': 'combined', 'model': {
        'provider': 'ollama', 'name': 'my-model:latest', 'endpoint': 'http://localhost:11434'}})
    plan = build_plan(selection)
    assert plan.environment['ODESSY_MODEL_NAME'] == 'my-model:latest'
    assert plan.environment['ODESSY_OLLAMA_URL'] == 'http://localhost:11434'
    assert plan.environment['ODESSY_DEPLOYMENT'] == 'single'
    assert not plan.downloads
    assert build_plan(selection).plan_id == plan.plan_id


def test_cloud_only_has_no_local_model_or_voice_components():
    plan = build_plan(SetupSelection.from_dict({'role': 'coordinator', 'model': {
        'provider': 'deepseek', 'name': 'chosen-model', 'credential_env': 'DEEPSEEK_API_KEY'}}))
    assert not {'ollama', 'whisper.cpp', 'kokoro', 'node'} & set(plan.components)
    assert plan.environment['ODESSY_CLOUD_MODEL'] == 'chosen-model'
    assert plan.readiness == 'configured_unverified'


@pytest.mark.parametrize('data', [
    {'role': 'host'}, {'role': 'execution', 'model': {'provider': 'ollama', 'name': 'x'}},
    {'role': 'combined', 'model': {'provider': 'unknown', 'name': 'x'}},
    {'role': 'combined', 'model': {'provider': 'auto', 'name': 'x'}},
    {'role': 'combined', 'model': {'provider': 'ollama', 'name': 'x\nEVIL=yes'}},
    {'role': 'combined', 'voice': {'enabled': 'false'}},
    {'role': 'combined', 'api_key': 'secret'},
    {'role': 'combined', 'model': {'provider': 'ollama', 'name': 'x', 'endpoint': 'http://localhost:99999'}},
    {'role': 'combined', 'model': {'provider': 'ollama', 'name': 'x', 'endpoint': False}},
    {'role': 'combined', 'model': {'provider': 'ollama', 'name': 'x', 'credential_env': False}},
])
def test_invalid_or_unsupported_selection_is_explicit(data):
    with pytest.raises(ValueError):
        build_plan(SetupSelection.from_dict(data))


def test_voice_is_opt_in_and_model_changes_invalidate_plan():
    base = SetupSelection.from_dict({'role': 'combined', 'model': {'provider': 'ollama', 'name': 'one'}})
    changed = SetupSelection.from_dict({'role': 'combined', 'model': {'provider': 'ollama', 'name': 'two'}})
    assert build_plan(base).plan_id != build_plan(changed).plan_id
    assert not {'whisper.cpp', 'kokoro'} & set(build_plan(base).components)


def test_cloud_provider_names_match_runtime_adapters():
    from odessy.models.cloud_model import CLOUD_PROVIDERS as runtime
    from odessy.setup.contracts import CLOUD_PROVIDERS
    assert set(CLOUD_PROVIDERS) == set(runtime)
