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


def test_serve_refuses_without_a_launcher_token(tmp_path, monkeypatch):
    root = tmp_path / 'App'
    make_installation(root, model=True)
    monkeypatch.delenv('ODESSY_API_TOKEN', raising=False)
    with pytest.raises(ValueError, match='token'):
        installed.serve(['--root', str(root), '--port', '8765'])
