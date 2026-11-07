"""Opt-in voice installation: verification, safe extraction, no overwrite."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
import zipfile

import pytest

from odessy.setup import voice_install


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(cache: Path, name: str, data: bytes, **overrides) -> dict:
    source = _write(cache / 'source' / name, data)
    entry = {
        'id': name.replace('.', '-'),
        'label': name,
        'kind': 'file',
        'file': name,
        'field': 'whisper_model',
        'url': source.as_uri(),
        'sha256': _digest(source),
        'size_bytes': source.stat().st_size,
        'license': 'MIT',
        'platform': 'windows',
        'architecture': 'x86_64',
    }
    entry.update(overrides)
    return entry


def _manifest(tmp_path: Path, artifacts: list) -> Path:
    target = tmp_path / 'manifest.json'
    target.write_text(json.dumps({'schema_version': 2, 'artifacts': artifacts}),
                      encoding='utf-8')
    return target


@pytest.fixture(autouse=True)
def _windows(monkeypatch, tmp_path):
    monkeypatch.setattr(voice_install, '_platform', lambda: ('windows', 'x86_64'))
    # Never write into the developer's real wake-asset directory.
    monkeypatch.setenv('ODESSY_WAKE_ASSET_DIR', str(tmp_path / 'wake'))


def _wake_artifact(cache: Path, name: str, data: bytes, **overrides) -> dict:
    entry = _artifact(cache, name, data, field='wake_model', location='wake',
                      features=['wake'], license='CC-BY-NC-SA-4.0',
                      version='openWakeWord v0.5.1')
    entry.update(overrides)
    return entry


def _wheel_artifact(cache: Path, filename: str, data: bytes, features: list) -> dict:
    source = _write(cache / 'source' / filename, data)
    return {
        'id': f'py-{filename.split("-")[0]}',
        'label': filename,
        'kind': 'wheel',
        'file': filename,
        'url': source.as_uri(),
        'sha256': _digest(source),
        'size_bytes': source.stat().st_size,
        'license': 'MIT',
        'platform': 'windows',
        'architecture': 'x86_64',
        'features': features,
    }


class _OkResult:
    returncode = 0
    stdout = 'ok'
    stderr = ''


def _import_ok(argv, **_kwargs):
    """Stands in for an interpreter that can import the feature's modules."""
    return _OkResult()


def _zip(cache: Path, members: dict, *, prefix: str = 'Release/', name='whisper.zip') -> Path:
    path = cache / 'source' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w') as archive:
        for member, data in members.items():
            archive.writestr(f'{prefix}{member}', data)
    return path


# ------------------------------------------------------------------- manifest

def test_a_manifest_member_may_not_be_a_path(tmp_path):
    archive = _zip(tmp_path, {'a.exe': b'x'})
    with pytest.raises(ValueError, match='bare name'):
        voice_install.load_manifest(_manifest(tmp_path, [{
            'id': 'a', 'label': 'A', 'kind': 'archive', 'file': 'a.zip',
            'url': archive.as_uri(), 'sha256': 'a' * 64, 'size_bytes': 10,
            'license': 'MIT', 'platform': 'windows', 'architecture': 'x86_64',
            'field': 'whisper_bin',
            'members': ['..\\escape.exe'], 'strip_prefix': 'Release/', 'executable': 'a.exe',
        }]))


def test_an_archive_executable_must_be_one_of_its_members(tmp_path):
    archive = _zip(tmp_path, {'a.exe': b'x'})
    with pytest.raises(ValueError, match='extracted member'):
        voice_install.load_manifest(_manifest(tmp_path, [{
            'id': 'a', 'label': 'A', 'kind': 'archive', 'file': 'a.zip',
            'url': archive.as_uri(), 'sha256': 'a' * 64, 'size_bytes': 10,
            'license': 'MIT', 'platform': 'windows', 'architecture': 'x86_64',
            'field': 'whisper_bin',
            'members': ['a.exe'], 'strip_prefix': 'Release/', 'executable': 'b.exe',
        }]))


def test_an_unpinned_artifact_is_reported_rather_than_installed(tmp_path):
    entry = _artifact(tmp_path, 'model.bin', b'weights', sha256='')
    entries = voice_install.load_manifest(_manifest(tmp_path, [entry]))
    plan = voice_install.plan(manifest=entries, root=tmp_path / 'voice')
    assert plan['installable'] is False and plan['available'] == []
    assert 'checksum' in plan['unavailable'][0]['reason']
    with pytest.raises(ValueError):
        voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])


# --------------------------------------------------------------------- files

def test_install_downloads_verifies_and_records(tmp_path):
    entries = voice_install.load_manifest(
        _manifest(tmp_path, [_artifact(tmp_path, 'model.bin', b'weights')]))
    root = tmp_path / 'voice'
    plan = voice_install.plan(manifest=entries, root=root)
    result = voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    assert result['state'] == 'ready' and result['cancelled'] is False
    # Three separate facts, not one boolean.
    assert result['attempted'] is True and result['persisted'] is True
    assert result['verified'] is True and result['severity'] == 'info'
    assert (root / 'model.bin').read_bytes() == b'weights'
    assert result['paths'] == {'whisper_model': str(root / 'model.bin')}
    record = json.loads((root / voice_install.RECORD_NAME).read_text(encoding='utf-8'))
    assert record['artifacts'][0]['sha256'] == _digest(root / 'model.bin')
    assert not list(root.glob('*.partial'))


def test_a_stale_plan_is_refused(tmp_path):
    entries = voice_install.load_manifest(
        _manifest(tmp_path, [_artifact(tmp_path, 'model.bin', b'weights')]))
    plan = voice_install.plan(manifest=entries, root=tmp_path / 'voice')
    with pytest.raises(ValueError):
        voice_install.install({**plan, 'entries': entries}, confirm='not-the-plan')
    assert not (tmp_path / 'voice').exists()


def test_a_wrong_digest_leaves_nothing_behind(tmp_path):
    entries = voice_install.load_manifest(
        _manifest(tmp_path, [_artifact(tmp_path, 'model.bin', b'weights', sha256='0' * 64)]))
    plan = voice_install.plan(manifest=entries, root=tmp_path / 'voice')
    with pytest.raises(ValueError, match='verification'):
        voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    assert not (tmp_path / 'voice' / 'model.bin').exists()
    assert not list((tmp_path / 'voice').glob('*.partial'))


def test_an_existing_different_file_is_never_overwritten(tmp_path):
    root = tmp_path / 'voice'
    _write(root / 'model.bin', b'user-owned')
    entries = voice_install.load_manifest(
        _manifest(tmp_path, [_artifact(tmp_path, 'model.bin', b'weights')]))
    plan = voice_install.plan(manifest=entries, root=root)
    with pytest.raises(ValueError, match='not replaced'):
        voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    assert (root / 'model.bin').read_bytes() == b'user-owned'


def test_an_existing_verified_file_is_preserved_not_rewritten(tmp_path):
    root = tmp_path / 'voice'
    _write(root / 'model.bin', b'weights')
    entries = voice_install.load_manifest(
        _manifest(tmp_path, [_artifact(tmp_path, 'model.bin', b'weights')]))
    plan = voice_install.plan(manifest=entries, root=root)
    assert plan['total_bytes'] == 0 and plan['nothing_to_do'] is True
    result = voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    assert result['installed'] == [] and len(result['preserved']) == 1
    assert result['verified'] is True


# ------------------------------------------------------------------- archive

def test_an_archive_installs_only_its_members_as_real_files(tmp_path):
    archive = _zip(tmp_path, {'whisper-cli.exe': b'MZ-cli', 'whisper.dll': b'DLL',
                              'unrelated.dll': b'nope'})
    entry = {
        'id': 'whisper-cli', 'label': 'Whisper.cpp executable', 'kind': 'archive',
        'file': 'whisper.zip', 'url': archive.as_uri(), 'sha256': _digest(archive),
        'size_bytes': archive.stat().st_size, 'license': 'MIT', 'platform': 'windows',
        'architecture': 'x86_64', 'field': 'whisper_bin',
        'members': ['whisper-cli.exe', 'whisper.dll'], 'strip_prefix': 'Release/',
        'executable': 'whisper-cli.exe',
    }
    entries = voice_install.load_manifest(_manifest(tmp_path, [entry]))
    root = tmp_path / 'voice'
    plan = voice_install.plan(manifest=entries, root=root)
    result = voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    assert (root / 'whisper-cli.exe').read_bytes() == b'MZ-cli'
    assert (root / 'whisper.dll').read_bytes() == b'DLL'
    assert not (root / 'unrelated.dll').exists()
    # A ZIP is never saved as the executable, and the archive itself is not kept.
    assert not (root / 'whisper.zip').exists()
    assert not list(root.glob('*.partial'))
    assert result['paths'] == {'whisper_bin': str(root / 'whisper-cli.exe')}
    assert voice_install.status(manifest=entries, root=root)['components'][0]['state'] == 'ready'
    (root / 'whisper.dll').write_bytes(b'tampered')
    assert voice_install.status(manifest=entries, root=root)['components'][0]['state'] == 'unverified'
    plan = voice_install.plan(manifest=entries, root=root)
    with pytest.raises(ValueError, match='not replaced'):
        voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    assert (root / 'whisper.dll').read_bytes() == b'tampered'


def test_existing_unrecorded_archive_members_are_verified_before_trust(tmp_path):
    archive = _zip(tmp_path, {'whisper-cli.exe': b'MZ-cli', 'whisper.dll': b'DLL'})
    entry = {
        'id': 'whisper-cli', 'label': 'Whisper', 'kind': 'archive',
        'file': 'whisper.zip', 'url': archive.as_uri(), 'sha256': _digest(archive),
        'size_bytes': archive.stat().st_size, 'license': 'MIT', 'platform': 'windows',
        'architecture': 'x86_64', 'field': 'whisper_bin',
        'members': ['whisper-cli.exe', 'whisper.dll'], 'strip_prefix': 'Release/',
        'executable': 'whisper-cli.exe',
    }
    entries = voice_install.load_manifest(_manifest(tmp_path, [entry]))
    root = tmp_path / 'voice'
    _write(root / 'whisper-cli.exe', b'MZ-cli')
    _write(root / 'whisper.dll', b'user-file')
    plan = voice_install.plan(manifest=entries, root=root)
    with pytest.raises(ValueError, match='not replaced'):
        voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    assert (root / 'whisper.dll').read_bytes() == b'user-file'


def test_an_archive_missing_a_required_member_is_refused(tmp_path):
    archive = _zip(tmp_path, {'whisper-cli.exe': b'MZ-cli'})
    entry = {
        'id': 'whisper-cli', 'label': 'Whisper.cpp executable', 'kind': 'archive',
        'file': 'whisper.zip', 'url': archive.as_uri(), 'sha256': _digest(archive),
        'size_bytes': archive.stat().st_size, 'license': 'MIT', 'platform': 'windows',
        'architecture': 'x86_64', 'field': 'whisper_bin',
        'members': ['whisper-cli.exe', 'ggml.dll'], 'strip_prefix': 'Release/',
        'executable': 'whisper-cli.exe',
    }
    entries = voice_install.load_manifest(_manifest(tmp_path, [entry]))
    plan = voice_install.plan(manifest=entries, root=tmp_path / 'voice')
    with pytest.raises(ValueError, match='does not contain ggml.dll'):
        voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    assert not (tmp_path / 'voice' / 'whisper-cli.exe').exists()


# --------------------------------------------------------------------- wheels

def test_wheels_are_cached_for_the_installed_runtime(tmp_path):
    entry = _artifact(tmp_path, 'demo-1.0-py3-none-any.whl', b'PK-wheel',
                      kind='wheel', field=None, id='py-demo')
    entry.pop('field')
    entries = voice_install.load_manifest(_manifest(tmp_path, [entry]))
    root = tmp_path / 'voice'
    plan = voice_install.plan(manifest=entries, root=root)
    result = voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    assert (root / 'wheels' / 'demo-1.0-py3-none-any.whl').is_file()
    assert result['wheel_count'] == 1
    # Nothing is installed into a runtime yet, and that is not a failure.
    assert result['verified'] is True


def _installed_runtime(tmp_path: Path) -> Path:
    root = tmp_path / 'App'
    version = 'a' * 32
    relative = Path('Scripts/python.exe') if os.name == 'nt' else Path('bin/python3')
    interpreter = root / 'versions' / version / relative
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b'MZ')
    (root / 'installation.json').write_text(
        json.dumps({'schema_version': 1, 'active': version}), encoding='utf-8')
    return root


def test_the_runtime_install_is_offline_and_hash_locked(tmp_path):
    entry = _artifact(tmp_path, 'demo-1.0-py3-none-any.whl', b'PK-wheel',
                      kind='wheel', id='py-demo')
    entry.pop('field')
    entries = voice_install.load_manifest(_manifest(tmp_path, [entry]))
    root = tmp_path / 'voice'
    plan = voice_install.plan(manifest=entries, root=root)
    voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    calls = []

    class Result:
        returncode = 0
        stdout = 'Successfully installed demo-1.0'
        stderr = ''

    def runner(argv, **_kwargs):
        calls.append(argv)
        return Result()

    installed = voice_install.python_install(_installed_runtime(tmp_path), root,
                                             entries=entries, runner=runner)
    assert installed['verified'] is True and installed['installed'] is True
    install_argv = calls[0]
    assert '--no-index' in install_argv and '--require-hashes' in install_argv
    requirement = (root / 'wheels' / voice_install.REQUIREMENTS_NAME).read_text(encoding='utf-8')
    assert 'demo==1.0 --hash=sha256:' + entry['sha256'] in requirement
    # The check runs in the installed interpreter, not the helper's own.
    assert calls[1][0] == installed['python']


def test_a_failed_runtime_install_reports_an_error_and_a_remedy(tmp_path):
    entry = _artifact(tmp_path, 'demo-1.0-py3-none-any.whl', b'PK-wheel',
                      kind='wheel', id='py-demo')
    entry.pop('field')
    entries = voice_install.load_manifest(_manifest(tmp_path, [entry]))
    root = tmp_path / 'voice'
    plan = voice_install.plan(manifest=entries, root=root)
    voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])

    class Result:
        returncode = 1
        stdout = ''
        stderr = 'ERROR: no matching distribution found'

    result = voice_install.python_install(_installed_runtime(tmp_path), root, entries=entries,
                                          runner=lambda argv, **kwargs: Result())
    assert result['installed'] is False and result['verified'] is False
    assert result['severity'] == 'error'
    assert 'no matching distribution' in ' '.join(result['log'])
    assert result['manual_step']


def test_runtime_install_requires_an_installed_runtime(tmp_path):
    (tmp_path / 'voice' / 'wheels').mkdir(parents=True)
    (tmp_path / 'voice' / 'wheels' / 'demo-1.0-py3-none-any.whl').write_bytes(b'PK')
    with pytest.raises(ValueError, match='no installed Odessy runtime'):
        voice_install.python_install(tmp_path / 'missing', tmp_path / 'voice', entries=[])


# -------------------------------------------------------------- space, cancel

def test_insufficient_space_is_refused_before_downloading(tmp_path, monkeypatch):
    entries = voice_install.load_manifest(
        _manifest(tmp_path, [_artifact(tmp_path, 'model.bin', b'weights')]))
    plan = voice_install.plan(manifest=entries, root=tmp_path / 'voice')
    monkeypatch.setattr(voice_install.shutil, 'disk_usage',
                        lambda _: type('U', (), {'free': 1})())
    with pytest.raises(ValueError, match='insufficient space'):
        voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    assert not (tmp_path / 'voice' / 'model.bin').exists()


def test_a_tight_disk_states_the_headroom_before_downloading(tmp_path, monkeypatch):
    entries = voice_install.load_manifest(
        _manifest(tmp_path, [_artifact(tmp_path, 'model.bin', b'weights' * 100)]))
    plan = voice_install.plan(manifest=entries, root=tmp_path / 'voice')
    needed = plan['total_bytes']
    monkeypatch.setattr(voice_install.shutil, 'disk_usage',
                        lambda _: type('U', (), {'free': int(needed * 1.1)})())
    tight = voice_install.plan(manifest=entries, root=tmp_path / 'voice')
    assert 'would remain free' in tight['space_warning']


def test_unmeasurable_free_space_is_silent_rather_than_guessed(tmp_path, monkeypatch):
    def unmeasurable(_path):
        raise OSError('unsupported filesystem')

    monkeypatch.setattr(voice_install.shutil, 'disk_usage', unmeasurable)
    entries = voice_install.load_manifest(
        _manifest(tmp_path, [_artifact(tmp_path, 'model.bin', b'weights')]))
    plan = voice_install.plan(manifest=entries, root=tmp_path / 'voice')
    assert plan['free_bytes'] is None and plan['space_warning'] == ''


def test_cancelling_removes_the_partial_download(tmp_path):
    entries = voice_install.load_manifest(
        _manifest(tmp_path, [_artifact(tmp_path, 'model.bin', b'weights')]))
    plan = voice_install.plan(manifest=entries, root=tmp_path / 'voice')
    result = voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'],
                                   cancelled=lambda: True)
    assert result['cancelled'] is True and result['state'] == 'cancelled'
    assert result['persisted'] is False
    assert not (tmp_path / 'voice' / 'model.bin').exists()
    assert not list((tmp_path / 'voice').glob('*.partial'))


# --------------------------------------------------------------------- status

def test_status_reports_missing_then_ready_with_findings(tmp_path):
    entries = voice_install.load_manifest(
        _manifest(tmp_path, [_artifact(tmp_path, 'model.bin', b'weights')]))
    root = tmp_path / 'voice'
    empty = voice_install.status(manifest=entries, root=root)
    assert empty['ready'] is False and empty['missing'] == ['model-bin']
    finding = next(item for item in empty['findings'] if item['check_id'].endswith('model-bin'))
    assert finding['severity'] == 'warning' and 'Download' in finding['fix_hint']
    plan = voice_install.plan(manifest=entries, root=root)
    voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    ready = voice_install.status(manifest=entries, root=root)
    assert ready['components'][0]['state'] == 'ready'
    # The components are ready, but no Python packages were downloaded, so the
    # overall answer stays a warning instead of claiming voice works.
    assert ready['severity'] == 'warning'
    assert ready['python']['severity'] == 'warning'


def test_a_component_that_does_not_match_its_recorded_digest_is_an_error(tmp_path):
    entries = voice_install.load_manifest(
        _manifest(tmp_path, [_artifact(tmp_path, 'model.bin', b'weights')]))
    root = tmp_path / 'voice'
    plan = voice_install.plan(manifest=entries, root=root)
    voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    (root / 'model.bin').write_bytes(b'tampered')
    report = voice_install.status(manifest=entries, root=root)
    assert report['components'][0]['state'] == 'unverified'
    assert report['severity'] == 'error' and report['ready'] is False
    assert 'remove' in report['components'][0]['fix_hint']


def test_an_unsupported_platform_reports_the_limitation(monkeypatch, tmp_path):
    monkeypatch.setattr(voice_install, '_platform', lambda: ('linux', 'x86_64'))
    entries = voice_install.load_manifest(
        _manifest(tmp_path, [_artifact(tmp_path, 'model.bin', b'weights')]))
    plan = voice_install.plan(manifest=entries, root=tmp_path / 'voice')
    assert plan['installable'] is False
    assert 'not available on linux' in plan['unavailable'][0]['reason']


def test_the_bridge_uses_its_own_voice_root_and_ignores_a_caller_path(tmp_path, monkeypatch):
    from odessy.setup.native_bridge import manage

    monkeypatch.setenv('ODESSY_VOICE_ROOT', str(tmp_path / 'voice'))
    plan = manage({'action': 'voice-plan', 'root': 'ignored', 'payload': '', 'selection': {}})
    assert plan['root'] == str(tmp_path / 'voice')
    status = manage({'action': 'voice-status', 'root': 'ignored', 'install_root': ''})
    assert status['root'] == str(tmp_path / 'voice')
    fields = {item['field'] for item in plan['available'] if item['kind'] != 'wheel'}
    assert fields == set(voice_install.COMPONENT_FIELDS)
    assert any(item['kind'] == 'wheel' for item in plan['available'])


# ----------------------------------------------------------------- wake word

def test_a_wake_plan_offers_only_the_wake_feature(tmp_path):
    entries = voice_install.load_manifest(_manifest(tmp_path, [
        _artifact(tmp_path, 'model.bin', b'weights'),
        _wake_artifact(tmp_path, 'hey_jarvis_v0.1.onnx', b'wake-weights'),
    ]))
    wake = voice_install.plan(manifest=entries, root=tmp_path / 'voice', features=['wake'])
    assert [item['id'] for item in wake['available']] == ['hey_jarvis_v0-1-onnx']
    assert wake['features'] == ['wake']
    assert wake['wake_root'] == str(tmp_path / 'wake')
    voice = voice_install.plan(manifest=entries, root=tmp_path / 'voice', features=['voice'])
    assert [item['id'] for item in voice['available']] == ['model-bin']
    # A plan is bound to its feature set, so the wrong selection cannot install it.
    with pytest.raises(ValueError):
        voice_install.install({**wake, 'entries': entries}, confirm=voice['plan_id'])


def test_an_unknown_feature_is_refused(tmp_path):
    entries = voice_install.load_manifest(
        _manifest(tmp_path, [_artifact(tmp_path, 'model.bin', b'weights')]))
    with pytest.raises(ValueError, match='unsupported speech feature'):
        voice_install.plan(manifest=entries, root=tmp_path / 'voice', features=['wakeword'])


def test_the_wake_model_lands_where_the_runtime_reads_it(tmp_path):
    from odessy.voice import assets

    entries = voice_install.load_manifest(_manifest(tmp_path, [
        _wake_artifact(tmp_path, 'hey_jarvis_v0.1.onnx', b'wake-weights')]))
    root = tmp_path / 'voice'
    plan = voice_install.plan(manifest=entries, root=root, features=['wake'])
    result = voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    model = tmp_path / 'wake' / 'hey_jarvis_v0.1.onnx'
    assert model.read_bytes() == b'wake-weights'
    assert result['paths'] == {'wake_model': str(model)}
    # The runtime's own loader must see it as verified, or the detector would
    # refuse a model the installer just claimed to have installed.
    recorded = assets.load_manifest(tmp_path / 'wake')
    assert recorded == {'hey_jarvis_v0.1.onnx': True}
    manifest = json.loads((tmp_path / 'wake' / 'assets.json').read_text(encoding='utf-8'))
    record = manifest['assets'][0]
    assert record['license'] == 'CC-BY-NC-SA-4.0'
    assert record['version'] == 'openWakeWord v0.5.1'
    assert record['sha256_observed'] == record['sha256']


def test_writing_the_wake_manifest_keeps_a_users_other_models(tmp_path):
    wake = tmp_path / 'wake'
    wake.mkdir()
    (wake / 'assets.json').write_text(json.dumps({'assets': [
        {'name': 'custom_model.onnx', 'verified': True, 'sha256': 'a' * 64}]}), encoding='utf-8')
    entries = voice_install.load_manifest(_manifest(tmp_path, [
        _wake_artifact(tmp_path, 'hey_jarvis_v0.1.onnx', b'wake-weights')]))
    plan = voice_install.plan(manifest=entries, root=tmp_path / 'voice', features=['wake'])
    voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    names = [item['name'] for item in
             json.loads((wake / 'assets.json').read_text(encoding='utf-8'))['assets']]
    assert 'custom_model.onnx' in names and 'hey_jarvis_v0.1.onnx' in names


def test_wake_readiness_needs_the_model_and_openwakeword(tmp_path):
    entries = voice_install.load_manifest(_manifest(tmp_path, [
        _wake_artifact(tmp_path, 'hey_jarvis_v0.1.onnx', b'wake-weights'),
        _wheel_artifact(tmp_path, 'openwakeword-0.6.0-py3-none-any.whl', b'PK', ['wake']),
    ]))
    root = tmp_path / 'voice'
    plan = voice_install.plan(manifest=entries, root=root, features=['wake'])
    voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])

    # Model present, but nothing installed into a runtime yet.
    pending = voice_install.status(manifest=entries, root=root, features=['wake'])
    assert pending['wake']['ready'] is False
    assert pending['readiness'] == {'wake': False}
    assert 'not ready' in pending['wake']['message']

    installed = voice_install.status(manifest=entries, root=root, features=['wake'],
                                     install_root=_installed_runtime(tmp_path),
                                     runner=_import_ok)
    assert installed['wake']['ready'] is True
    assert installed['readiness'] == {'wake': True}
    assert installed['ready'] is True


def test_wake_readiness_requires_loading_the_detector(tmp_path):
    entries = voice_install.load_manifest(_manifest(tmp_path, [
        _wake_artifact(tmp_path, 'hey_jarvis_v0.1.onnx', b'wake-weights'),
        _wheel_artifact(tmp_path, 'openwakeword-0.6.0-py3-none-any.whl', b'PK', ['wake']),
    ]))
    root = tmp_path / 'voice'
    plan = voice_install.plan(manifest=entries, root=root, features=['wake'])
    voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])

    def missing_resources(command, **kwargs):
        if 'OpenWakeWordDetector' in ' '.join(command):
            return subprocess.CompletedProcess(command, 1, '', 'missing model resources')
        return _import_ok(command, **kwargs)

    report = voice_install.status(manifest=entries, root=root, features=['wake'],
                                  install_root=_installed_runtime(tmp_path),
                                  runner=missing_resources)
    assert report['readiness']['wake'] is False
    assert 'not ready' in report['wake']['message']


def test_a_tampered_wake_model_is_an_error_not_readiness(tmp_path):
    entries = voice_install.load_manifest(_manifest(tmp_path, [
        _wake_artifact(tmp_path, 'hey_jarvis_v0.1.onnx', b'wake-weights')]))
    root = tmp_path / 'voice'
    plan = voice_install.plan(manifest=entries, root=root, features=['wake'])
    voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    (tmp_path / 'wake' / 'hey_jarvis_v0.1.onnx').write_bytes(b'swapped')
    report = voice_install.status(manifest=entries, root=root, features=['wake'])
    assert report['components'][0]['state'] == 'unverified'
    assert report['severity'] == 'error' and report['wake']['ready'] is False
    replanned = voice_install.plan(manifest=entries, root=root, features=['wake'])
    assert replanned['nothing_to_do'] is False


def test_a_wake_only_install_installs_only_the_wake_wheels(tmp_path):
    entries = voice_install.load_manifest(_manifest(tmp_path, [
        _wake_artifact(tmp_path, 'hey_jarvis_v0.1.onnx', b'wake-weights'),
        _wheel_artifact(tmp_path, 'openwakeword-0.6.0-py3-none-any.whl', b'wake-wheel', ['wake']),
        _wheel_artifact(tmp_path, 'kokoro_onnx-0.6.1-py3-none-any.whl', b'voice-wheel', ['voice']),
    ]))
    root = tmp_path / 'voice'
    plan = voice_install.plan(manifest=entries, root=root, features=['wake'])
    voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    calls = []

    class Result:
        returncode = 0
        stdout = 'ok'
        stderr = ''

    def runner(argv, **_kwargs):
        calls.append(argv)
        return Result()

    result = voice_install.python_install(_installed_runtime(tmp_path), root, entries=entries,
                                          features=['wake'], runner=runner)
    assert result['verified'] is True and result['features'] == ['wake']
    requirement = (root / 'wheels' / voice_install.REQUIREMENTS_NAME).read_text(encoding='utf-8')
    assert 'openwakeword==0.6.0' in requirement
    assert 'kokoro_onnx' not in requirement
    assert 'openwakeword' in ' '.join(calls[0]) or '--require-hashes' in calls[0]


def test_the_shipped_wake_pin_matches_the_runtime_table():
    from odessy.voice.assets import WAKE_ASSETS

    entries = voice_install.load_manifest(voice_install.manifest_path())
    wake = [entry for entry in entries if entry['location'] == 'wake']
    assert len(wake) == len(WAKE_ASSETS)
    for entry, asset in zip(wake, WAKE_ASSETS):
        assert entry['file'] == asset.name
        assert entry['sha256'] == asset.sha256
        assert entry['license'] == asset.license
        assert entry['features'] == ('wake',)
        assert entry['size_bytes'] > 0


def test_the_wake_directory_mirrors_the_runtime_default(monkeypatch):
    from odessy.setup import voice_assets
    from odessy.voice import assets

    monkeypatch.delenv('ODESSY_WAKE_ASSET_DIR', raising=False)
    monkeypatch.delenv('ODESSY_WAKE_MODEL', raising=False)
    assert voice_install.wake_root() == voice_assets.wake_directory()
    assert voice_install.wake_root() == assets.asset_dir()


@pytest.mark.parametrize("shared_location", ["voice", "wake"])
def test_another_process_holds_a_shared_asset_root(tmp_path, shared_location, monkeypatch):
    import os
    import sys
    monkeypatch.delenv('PYTHONPATH', raising=False)
    shared = tmp_path / shared_location
    code = ('import sys; from pathlib import Path; sys.path.insert(0, sys.argv[2]); '
            'from odessy.setup.voice_install import _operation_locks; '
            'lock = _operation_locks([Path(sys.argv[1])]); lock.__enter__(); '
            'print("locked", flush=True); sys.stdin.readline()')
    source = str(Path(voice_install.__file__).resolve().parents[2])
    child = subprocess.Popen([sys.executable, '-c', code, str(shared), source],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=os.environ.copy())
    try:
        assert child.stdout.readline().strip() == 'locked'
        name = 'wake.onnx' if shared_location == 'wake' else 'model.bin'
        entry = (_wake_artifact(tmp_path, name, b'model')
                 if shared_location == 'wake' else _artifact(tmp_path, name, b'model'))
        entries = voice_install.load_manifest(_manifest(tmp_path, [entry]))
        plan = voice_install.plan(manifest=entries,
            root=tmp_path / ('other-voice' if shared_location == 'wake' else 'voice'))
        with pytest.raises(ValueError, match='already running'):
            voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
        assert not (shared / name).exists()
    finally:
        child.communicate('done\n', timeout=10)


def test_download_preserves_other_operation_partial_and_a_new_destination(tmp_path, monkeypatch):
    entry = _artifact(tmp_path, 'model.bin', b'verified')
    entries = voice_install.load_manifest(_manifest(tmp_path, [entry]))
    root = tmp_path / 'voice'
    root.mkdir()
    foreign = root / 'model.bin.partial'
    foreign.write_bytes(b'other operation')
    original = voice_install._download
    def download(*args):
        original(*args)
        (root / 'model.bin').write_bytes(b'user changed file')
    monkeypatch.setattr(voice_install, '_download', download)
    plan = voice_install.plan(manifest=entries, root=root)
    with pytest.raises((ValueError, FileExistsError)):
        voice_install.install({**plan, 'entries': entries}, confirm=plan['plan_id'])
    assert (root / 'model.bin').read_bytes() == b'user changed file'
    assert foreign.read_bytes() == b'other operation'


def test_locks_release_after_cancellation_and_partial_acquisition(tmp_path):
    roots = [tmp_path / 'a', tmp_path / 'b']
    with voice_install._operation_locks([roots[1]]):
        with pytest.raises(ValueError, match='already running'):
            with voice_install._operation_locks(roots):
                pass
        with voice_install._operation_locks([roots[0]]):
            pass
    with pytest.raises(ValueError, match='cancelled'):
        with voice_install._operation_locks(roots, cancelled=lambda: True):
            pass
    with voice_install._operation_locks(roots):
        pass


def test_json_writes_do_not_touch_another_operations_temporary(tmp_path, monkeypatch):
    target = tmp_path / 'progress.json'
    foreign = tmp_path / 'progress.json.partial'
    foreign.write_bytes(b'other operation')
    original = voice_install.os.replace
    def fail_replace(source, destination):
        assert Path(source) != foreign
        raise OSError('interrupted publication')
    monkeypatch.setattr(voice_install.os, 'replace', fail_replace)
    with pytest.raises(OSError):
        voice_install._write_json(target, {'state': 'ready'})
    assert foreign.read_bytes() == b'other operation'
    assert list(tmp_path.glob('*.partial')) == [foreign]
    monkeypatch.setattr(voice_install.os, 'replace', original)
    voice_install._write_json(target, {'state': 'ready'})
    assert json.loads(target.read_text()) == {'state': 'ready'}
    assert foreign.read_bytes() == b'other operation'
