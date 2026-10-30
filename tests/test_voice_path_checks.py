"""Field-specific path verdicts, produced by the helper that owns the filesystem."""
import os
from pathlib import Path

import pytest


def test_a_folder_in_the_whisper_field_gets_the_specific_message(tmp_path):
    from odessy.setup.voice_assets import check_voice_paths
    issues = check_voice_paths({'voice': {'whisper_bin': str(tmp_path)}})
    assert issues['whisper_bin'] == {
        'message': 'Select the Whisper executable, not a folder.', 'blocking': True}


def test_folders_are_named_by_what_the_field_holds(tmp_path):
    from odessy.setup.voice_assets import check_voice_paths
    issues = check_voice_paths({'voice': {
        'tts_executable': str(tmp_path),
        'kokoro_model': str(tmp_path),
        'wake_model': str(tmp_path)}})
    assert issues['tts_executable']['message'] == 'Select an executable file, not a folder.'
    assert issues['kokoro_model']['message'] == 'Select a file, not a folder.'
    assert issues['wake_model']['message'] == 'Select a file, not a folder.'
    assert all(issue['blocking'] for issue in issues.values())


def test_a_missing_path_is_advisory_so_an_unmounted_volume_still_saves(tmp_path):
    from odessy.setup.voice_assets import check_voice_paths
    issues = check_voice_paths({'voice': {
        'whisper_bin': str(tmp_path / 'gone/whisper-cli'),
        'kokoro_model': str(tmp_path / 'gone/kokoro.onnx')}})
    assert issues['whisper_bin']['blocking'] is False
    assert issues['kokoro_model']['blocking'] is False


def test_a_bare_command_name_is_not_treated_as_a_broken_path(monkeypatch):
    from odessy.setup import voice_assets
    monkeypatch.setattr(voice_assets.shutil, 'which', lambda name: f'/usr/bin/{name}')
    assert voice_assets.check_voice_paths({'voice': {'whisper_bin': 'whisper-cli'}}) == {}
    monkeypatch.setattr(voice_assets.shutil, 'which', lambda name: None)
    issues = voice_assets.check_voice_paths({'voice': {'whisper_bin': 'whisper-cli'}})
    assert issues['whisper_bin']['blocking'] is False


@pytest.mark.skipif(os.name == 'nt', reason='execute permission is a POSIX concept')
def test_a_non_executable_binary_blocks_on_unix(tmp_path):
    from odessy.setup.voice_assets import check_voice_paths
    binary = tmp_path / 'whisper-cli'
    binary.write_text('#!/bin/sh\n')
    binary.chmod(0o644)
    issues = check_voice_paths({'voice': {'whisper_bin': str(binary)}})
    assert issues['whisper_bin'] == {'message': 'This file exists but is not executable.',
                                     'blocking': True}
    binary.chmod(0o755)
    assert check_voice_paths({'voice': {'whisper_bin': str(binary)}}) == {}


@pytest.mark.skipif(os.name == 'nt', reason='execute permission is a POSIX concept')
def test_an_executable_is_accepted_on_unix(tmp_path):
    from odessy.setup.voice_assets import check_voice_paths
    binary = tmp_path / 'whisper-cli'
    binary.write_text('#!/bin/sh\n')
    binary.chmod(0o755)
    model = tmp_path / 'ggml-tiny.bin'
    model.write_bytes(b'x')
    assert check_voice_paths({'voice': {'whisper_bin': str(binary),
                                        'whisper_model': str(model)}}) == {}


def test_empty_and_blank_values_are_not_issues():
    from odessy.setup.voice_assets import check_voice_paths
    assert check_voice_paths({'voice': {'whisper_bin': '', 'wake_model': '   '}}) == {}
    assert check_voice_paths({}) == {}


def test_relative_wake_directory_is_not_a_model(tmp_path, monkeypatch):
    from odessy.setup.voice_assets import check_voice_paths
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'models').mkdir()
    assert check_voice_paths({'voice': {'wake_model': 'models'}})['wake_model']['blocking'] is True


def test_stt_folder_error_is_reported_before_microphone_capture(tmp_path, monkeypatch):
    from odessy.setup import diagnostics
    from odessy.setup.contracts import SetupSelection
    def no_capture(*args):
        raise AssertionError('Invalid configuration must not open the microphone')
    monkeypatch.setattr(diagnostics, 'sample_audio', no_capture)
    selection = SetupSelection.from_dict({'role': 'combined', 'voice': {'whisper_bin': str(tmp_path)}})
    with pytest.raises(diagnostics.DiagnosticInputError, match='Select the Whisper executable, not a folder'):
        diagnostics.probe(selection, 'stt', run=True, consent=True)


def test_the_bridge_offers_a_check_only_mode(tmp_path, monkeypatch):
    """`check` must return verdicts without walking any directory."""
    from odessy.setup import bridge, voice_assets
    calls = []
    monkeypatch.setattr(voice_assets, 'discover_voice_assets',
                        lambda selection: calls.append(selection) or {})
    result = bridge.execute({'operation': 'voice-discover', 'id': 'r1',
                             'selection': {'voice': {'whisper_bin': str(tmp_path)}},
                             'parameters': {'check': True}})
    assert calls == [], 'a check must not run the scan'
    assert result['issues']['whisper_bin']['message'] == (
        'Select the Whisper executable, not a folder.')


def test_the_scan_reports_issues_without_changing_its_existing_shape(tmp_path, monkeypatch):
    from odessy.setup import bridge
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    result = bridge.execute({'operation': 'voice-discover', 'id': 'r2',
                             'selection': {'voice': {'whisper_bin': str(tmp_path)}}})
    assert {'candidates', 'missing', 'partial', 'reason', 'issues'} <= set(result)
    assert 'whisper_bin' in result['issues']
    assert 'wake_model' in result['candidates']
