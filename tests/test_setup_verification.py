from dataclasses import replace

from odessy.setup.contracts import DiscoveryReport, InstalledModel, ModelSelection, SetupSelection, VoiceSelection
from odessy.setup.planning import build_plan
from odessy.setup.verification import verify


def test_voice_disabled_requires_no_optional_packages(monkeypatch):
    import odessy.setup.verification as module
    def forbidden(*args): raise AssertionError('optional voice probe despite text-only choice')
    monkeypatch.setattr(module.importlib.util, 'find_spec', forbidden)
    assert verify(SetupSelection(role='combined'))['voice']['status'] == 'disabled'


def test_missing_optional_voice_does_not_claim_ready_or_download(tmp_path):
    selection = SetupSelection(role='combined', voice=VoiceSelection(enabled=True))
    report = verify(selection)
    assert report['voice']['status'] == 'missing'
    assert 'whisper_bin' in report['voice']['missing']
    assert not report['ready'] and not build_plan(selection).downloads


def test_existing_voice_paths_are_not_inference_qualification(tmp_path, monkeypatch):
    import odessy.setup.verification as module
    paths = []
    for name in ['whisper', 'stt.bin', 'tts.onnx', 'voices.bin']:
        path = tmp_path / name; path.write_bytes(b'test fixture'); paths.append(str(path))
    monkeypatch.setattr(module.shutil, 'which', lambda value: paths[0])
    monkeypatch.setattr(module.importlib.util, 'find_spec', lambda name: object())
    selection = SetupSelection(role='combined', voice=VoiceSelection(True, *paths))
    report = verify(selection)
    assert report['voice']['status'] == 'assets_present_unverified'
    assert not report['ready']


def test_cloud_verification_checks_reference_without_sending_request(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'must-not-be-printed')
    selection = SetupSelection(role='coordinator', model=ModelSelection('deepseek', 'chosen'))
    report = verify(selection)
    assert report['model']['status'] == 'configured'
    assert 'must-not-be-printed' not in str(report)


def test_inventory_availability_does_not_establish_model_capabilities():
    selection = SetupSelection(role='combined', model=ModelSelection('ollama', 'chosen'))
    report = DiscoveryReport('ok', selection.model.endpoint, (InstalledModel('chosen'),))
    assert verify(selection, report)['model']['status'] == 'available_unverified'
    assert not verify(selection, report)['ready']


def test_plan_rejects_missing_installed_model_and_other_endpoint():
    import pytest
    selection = SetupSelection(role='combined', model=ModelSelection('ollama', 'chosen'))
    with pytest.raises(ValueError, match='not installed'):
        build_plan(selection, DiscoveryReport('empty', selection.model.endpoint))
    with pytest.raises(ValueError, match='different'):
        build_plan(selection, DiscoveryReport('ok', 'http://other:11434'))


def test_foreign_platform_is_not_verified_against_local_machine(monkeypatch):
    import pytest
    import platform
    import odessy.setup.verification as module
    foreign = 'linux' if platform.system() == 'Windows' else 'windows'
    def forbidden(*args): raise AssertionError('probed the wrong machine')
    monkeypatch.setattr(module.shutil, 'which', forbidden)
    with pytest.raises(ValueError, match='platform'):
        verify(SetupSelection(role='execution', platform=foreign))
