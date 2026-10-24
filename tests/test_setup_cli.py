import json

from odessy.setup.cli import main


def test_noninteractive_requires_explicit_role(capsys):
    assert main(['--non-interactive']) == 2
    assert 'role' in capsys.readouterr().err.lower()


def test_headless_execution_plan_needs_no_model(capsys):
    assert main(['--role', 'execution', '--non-interactive']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['selection']['role'] == 'execution'
    assert result['downloads'] == []
    assert result['readiness'] == 'needs_configuration'


def test_save_requires_exact_review_and_does_not_overwrite(tmp_path, capsys):
    output = tmp_path / 'setup.json'
    args = ['--role', 'combined', '--output', str(output)]
    assert main(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert not output.exists()
    assert main(args + ['--confirm-plan', plan['plan_id']]) == 0
    capsys.readouterr()
    assert json.loads(output.read_text())['selection']['role'] == 'combined'
    assert main(args + ['--confirm-plan', plan['plan_id']]) == 2
    assert json.loads(output.read_text())['selection']['role'] == 'combined'


def test_configuration_before_model_and_voice_setup(tmp_path, capsys):
    source = tmp_path / 'selection.json'
    source.write_text(json.dumps({'role': 'combined', 'model': {'provider': 'ollama', 'name': 'existing:q8'}}))
    assert main(['--config', str(source), '--verify']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['plan']['selection']['model']['name'] == 'existing:q8'
    assert report['verification']['model']['status'] == 'not_checked'


def test_setup_entrypoint_bypasses_broken_runtime_configuration(monkeypatch, capsys):
    import odessy.cli as cli
    monkeypatch.setenv('ODESSY_MODEL_BACKEND', 'invalid-unconfigured-runtime')
    def forbidden(*args): raise AssertionError('setup loaded runtime config')
    monkeypatch.setattr(cli, 'load_config_env', forbidden)
    try:
        cli.main(['setup', '--role', 'execution', '--non-interactive'])
    except SystemExit as outcome:
        assert outcome.code == 0
    assert json.loads(capsys.readouterr().out)['selection']['role'] == 'execution'


def test_lightweight_entrypoint_does_not_import_runtime(monkeypatch, capsys):
    import builtins
    from odessy.entrypoint import main as entrypoint
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if name == 'odessy.cli':
            raise AssertionError('setup imported the runtime CLI')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    try:
        entrypoint(['setup', '--role', 'execution', '--non-interactive'])
    except SystemExit as outcome:
        assert outcome.code == 0
    assert json.loads(capsys.readouterr().out)['selection']['role'] == 'execution'
