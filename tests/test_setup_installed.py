import json
import os
from pathlib import Path

import pytest

from odessy.setup import installed
from odessy.setup.maintenance import digest

# The installed layout is platform-specific: the record, the spec and the launcher
# have to agree on the same relative paths.
PYTHON_RELATIVE='Scripts/python.exe' if os.name=='nt' else 'bin/python3'
DESKTOP_RELATIVE='desktop/odessy_desktop.exe' if os.name=='nt' else 'desktop/odessy_desktop'


def make_installation(root, *, model=False, role='combined', configuration=None):
    version = 'a' * 32
    identity = 'b' * 32
    base = root / 'versions' / version
    (base / Path(PYTHON_RELATIVE).parent).mkdir(parents=True)
    (base / Path(DESKTOP_RELATIVE).parent).mkdir()
    (base / PYTHON_RELATIVE).write_bytes(b'python')
    (base / DESKTOP_RELATIVE).write_bytes(b'desktop')
    selection = {'role': role}
    if model:
        selection['model'] = {'provider': 'ollama', 'name': 'llama3', 'endpoint': 'http://127.0.0.1:11434'}
    (base / 'selection.json').write_text(json.dumps({'schema_version': 1, 'selection': selection}))
    files = {path.relative_to(base).as_posix(): digest(path) for path in base.rglob('*') if path.is_file()}
    (root / 'installation.json').write_text(json.dumps(
        {'schema_version': 1, 'id': identity, 'root': str(root), 'active': version,
         'versions': {version: files}, 'selection': selection}))
    if configuration is not None:
        (root / 'configuration.json').write_text(json.dumps(configuration))
    return version, identity, selection


def test_spec_reports_unconfigured_installation_without_starting_anything(tmp_path):
    root = tmp_path / 'App'
    version, identity, _ = make_installation(root)
    info = installed.spec(root)
    assert info['installation_id'] == identity and info['version'] == version
    assert info['model_configured'] is False and info['ready'] is False
    assert info['configuration_source'] == 'version'
    assert info['workspace'] == str(installed.default_workspace())
    assert info['desktop'].endswith(Path(DESKTOP_RELATIVE).name)


def test_spec_prefers_recorded_configuration_and_requires_intact_executables(tmp_path):
    root = tmp_path / 'App'
    make_installation(root, model=True)
    recorded = {'schema_version': 1, 'selection': {'role': 'combined', 'model': {
        'provider': 'ollama', 'name': 'qwen2.5:7b', 'endpoint': 'http://127.0.0.1:11434'}},
        'workspace': str(tmp_path / 'work')}
    (root / 'configuration.json').write_text(json.dumps(recorded))
    info = installed.spec(root)
    assert info['model_configured'] and info['selection']['model']['name'] == 'qwen2.5:7b'
    assert info['configuration_source'] == 'configuration'
    (root / 'versions' / info['version'] / DESKTOP_RELATIVE).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='modified or damaged'):
        installed.spec(root)


def test_spec_rejects_unversioned_and_legacy_roots(tmp_path):
    with pytest.raises(ValueError, match='no manageable'):
        installed.spec(tmp_path / 'missing')
    legacy = tmp_path / 'legacy'
    (legacy / '.installer').mkdir(parents=True)
    (legacy / '.installer' / 'state.json').write_text('{}')
    with pytest.raises(ValueError, match='no manageable'):
        installed.spec(legacy)


def test_serve_requires_model_and_authenticated_token(tmp_path, monkeypatch):
    root = tmp_path / 'App'
    make_installation(root)
    calls = []
    monkeypatch.setattr('odessy.setup.service_entry.run', lambda *a, **k: calls.append((a, k)))
    monkeypatch.setenv('ODESSY_API_TOKEN', 'x' * 32)
    with pytest.raises(ValueError, match='no model is configured'):
        installed.serve(['--root', str(root), '--port', '8765'])
    assert calls == []


def test_serve_starts_coordinator_with_recorded_configuration(tmp_path, monkeypatch):
    root = tmp_path / 'App'
    make_installation(root, model=True)
    workspace = tmp_path / 'work'
    (root / 'configuration.json').write_text(json.dumps({'schema_version': 1,
        'selection': {'role': 'combined', 'model': {'provider': 'ollama', 'name': 'llama3'}},
        'workspace': str(workspace)}))
    calls = []
    monkeypatch.setattr('odessy.setup.service_entry.run', lambda selection, **kwargs: calls.append((selection, kwargs)))
    monkeypatch.setenv('ODESSY_API_TOKEN', 't' * 32)
    installed.serve(['--root', str(root), '--port', '18765', '--exit-on-stdin-close'])
    selection, kwargs = calls[0]
    assert selection.model.name == 'llama3'
    assert kwargs['port'] == 18765 and kwargs['watch_stdin'] is True
    assert kwargs['token'] == 't' * 32
    assert kwargs['workspace'] == workspace and workspace.is_dir()


def test_serve_exports_the_staged_harness_runtime_of_the_active_version(tmp_path, monkeypatch):
    root = tmp_path / 'App'
    version, _, _ = make_installation(root, model=True)
    base = root / 'versions' / version
    (base / 'harness').mkdir()
    (base / 'harness' / 'harness-runtime.json').write_text('{}')
    (base / 'node').mkdir()
    (base / 'node' / 'node.exe').write_bytes(b'node')
    (base / 'tools' / 'dist' / 'src').mkdir(parents=True)
    (base / 'tools' / 'dist' / 'src' / 'server.js').write_text('x')
    calls = []
    monkeypatch.setattr('odessy.setup.service_entry.run', lambda selection, **kwargs: calls.append((selection, kwargs)))
    monkeypatch.setenv('ODESSY_API_TOKEN', 't' * 32)
    # The active version owns its runtime directory; an inherited value must not win.
    monkeypatch.setenv('ODESSY_HARNESS_RUNTIME_DIR', str(tmp_path / 'stale'))
    monkeypatch.delenv('ODESSY_HARNESS_NODE', raising=False)
    monkeypatch.delenv('ODESSY_NODE_TOOLS_DIR', raising=False)
    installed.serve(['--root', str(root), '--port', '18766'])
    assert calls, 'the coordinator must still start'
    assert os.environ['ODESSY_HARNESS_RUNTIME_DIR'] == str(base / 'harness')
    assert os.environ['ODESSY_HARNESS_NODE'] == str(base / 'node' / 'node.exe')
    assert os.environ['ODESSY_NODE_TOOLS_DIR'] == str(base / 'tools')


def test_serve_exports_no_harness_environment_without_a_staged_runtime(tmp_path, monkeypatch):
    root = tmp_path / 'App'
    make_installation(root, model=True)
    calls = []
    monkeypatch.setattr('odessy.setup.service_entry.run', lambda selection, **kwargs: calls.append((selection, kwargs)))
    monkeypatch.setenv('ODESSY_API_TOKEN', 't' * 32)
    monkeypatch.delenv('ODESSY_HARNESS_RUNTIME_DIR', raising=False)
    installed.serve(['--root', str(root), '--port', '18767'])
    assert calls
    assert 'ODESSY_HARNESS_RUNTIME_DIR' not in os.environ


def test_serve_refuses_without_a_launcher_token(tmp_path, monkeypatch):
    root = tmp_path / 'App'
    make_installation(root, model=True)
    monkeypatch.delenv('ODESSY_API_TOKEN', raising=False)
    with pytest.raises(ValueError, match='token'):
        installed.serve(['--root', str(root), '--port', '8765'])


def test_service_restart_loads_saved_ssh_target_before_monitoring(tmp_path, monkeypatch):
    from odessy.setup import service_entry
    from odessy.setup.contracts import SetupSelection
    from odessy.monitoring.settings import MonitoringSettings

    config = tmp_path / 'odessy.env'
    host_id = 'host_' + 'a' * 64
    config.write_text('ODESSY_SSH_COMPUTER=on\nODESSY_SSH_MONITORING=on\n'
                      f'ODESSY_SSH_ALIAS=saved-host\nODESSY_SSH_HOST_ID={host_id}\n')
    monkeypatch.setenv('ODESSY_CONFIG_FILE', str(config))
    for name in ('ODESSY_SSH_COMPUTER', 'ODESSY_SSH_MONITORING',
                 'ODESSY_SSH_ALIAS', 'ODESSY_SSH_HOST_ID'):
        monkeypatch.delenv(name, raising=False)
    # Shell values retain precedence over saved settings.
    monkeypatch.setenv('ODESSY_SSH_ALIAS', 'shell-host')
    observed = []

    async def server(**kwargs):
        observed.append(MonitoringSettings.from_env())

    monkeypatch.setattr('odessy.application.serve.run_server', server)
    selection = SetupSelection.from_dict({'role': 'combined', 'model': {
        'provider': 'ollama', 'name': 'fixture'}})
    service_entry.run(selection, workspace=tmp_path, token='fixture')
    assert observed[0].ssh_alias == 'shell-host'
    assert observed[0].ssh_host_id == host_id
    assert observed[0].ssh_monitoring
