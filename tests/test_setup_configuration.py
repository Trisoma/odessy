import json
from pathlib import Path

import pytest

from odessy.config.config_file import read_env_file
from odessy.setup.cli import main
from odessy.setup.configuration import export_environment, export_selection
from odessy.setup.contracts import ModelSelection, SetupSelection
from odessy.setup.planning import build_plan


def test_model_export_roundtrips_without_download_or_secret(tmp_path):
    selection = SetupSelection.from_dict({'role': 'combined', 'model': {
        'provider': 'ollama', 'name': 'user-model:q8', 'endpoint': 'http://127.0.0.1:9876'}})
    output = tmp_path / 'chosen.env'
    export_environment(build_plan(selection), output)
    assert read_env_file(output)['ODESSY_MODEL_NAME'] == 'user-model:q8'
    assert read_env_file(output)['ODESSY_OLLAMA_URL'] == 'http://127.0.0.1:9876'
    assert 'lfm2.5' not in output.read_text()


def test_unconfigured_model_cannot_create_accidental_lfm_runtime(tmp_path):
    output = tmp_path / 'unconfigured.env'
    with pytest.raises(ValueError, match='silently fall back'):
        export_environment(build_plan(SetupSelection(role='coordinator')), output)
    assert not output.exists()


def test_new_configuration_never_replaces_existing_file(tmp_path):
    output = tmp_path / 'existing.json'
    output.write_text('existing user data')
    with pytest.raises(ValueError):
        export_selection(build_plan(SetupSelection(role='combined')), output)
    assert output.read_text() == 'existing user data'


def test_plan_confirmation_binds_destination(tmp_path, capsys):
    first, second = tmp_path / 'one.json', tmp_path / 'two.json'
    assert main(['--role', 'execution', '--output', str(first)]) == 0
    identifier = json.loads(capsys.readouterr().out)['plan_id']
    assert main(['--role', 'execution', '--output', str(second), '--confirm-plan', identifier]) == 2
    assert not first.exists() and not second.exists()


def test_cli_saved_selection_roundtrip(tmp_path, capsys):
    output = tmp_path / 'setup.json'
    args = ['--role', 'combined', '--provider', 'ollama', '--model', 'user:q8', '--output', str(output)]
    assert main(args) == 0
    identifier = json.loads(capsys.readouterr().out)['plan_id']
    assert main(args + ['--confirm-plan', identifier]) == 0
    capsys.readouterr()
    assert main(['--config', str(output), '--verify']) == 0
    assert json.loads(capsys.readouterr().out)['plan']['selection']['model']['name'] == 'user:q8'


@pytest.mark.parametrize('payload', [
    '{"role":"execution","role":"combined"}',
    '{"schema_version":true,"selection":{"role":"combined"}}',
    '{"role":"combined","model":{"name":"x"}}',
    '{"role":"combined","model":{"provider":"custom","name":"x","api_key":"secret"}}',
])
def test_invalid_file_fails_without_exposing_contents(tmp_path, capsys, payload):
    source = tmp_path / 'input.json'; source.write_text(payload)
    assert main(['--config', str(source)]) == 2
    captured = capsys.readouterr()
    assert not captured.out and 'secret' not in captured.err


def test_write_race_does_not_replace_winner(tmp_path, monkeypatch):
    import odessy.setup.configuration as config
    original = config.os.link
    target = tmp_path / 'race.json'
    def race(source, destination):
        Path(destination).write_text('winner')
        return original(source, destination)
    monkeypatch.setattr(config.os, 'link', race)
    with pytest.raises(FileExistsError):
        export_selection(build_plan(SetupSelection(role='combined')), target)
    assert target.read_text() == 'winner'
    assert not list(tmp_path.glob('.odessy-setup-*'))


def test_modified_plan_cannot_be_exported_after_review(tmp_path):
    plan = build_plan(SetupSelection(role='combined', model=ModelSelection('ollama', 'chosen')))
    plan.environment['ODESSY_MODEL_NAME'] = 'changed-after-review'
    with pytest.raises(ValueError, match='changed'):
        export_environment(plan, tmp_path / 'tampered.env')
    assert not (tmp_path / 'tampered.env').exists()


def test_foreign_machine_selection_is_not_activated_locally(tmp_path):
    import platform
    foreign = 'windows' if platform.system() != 'Windows' else 'linux'
    selection = SetupSelection(role='combined', platform=foreign, model=ModelSelection('ollama', 'chosen'))
    with pytest.raises(ValueError, match='platform'):
        export_environment(build_plan(selection), tmp_path / 'foreign.env')


def test_disabled_voice_assets_are_staged_without_enabling_runtime():
    selection = SetupSelection.from_dict({'role': 'combined', 'voice': {'enabled': False, 'whisper_model': '/some/file'}})
    assert selection.voice.whisper_model == '/some/file'
    assert 'ODESSY_WHISPER_MODEL' not in build_plan(selection).environment
