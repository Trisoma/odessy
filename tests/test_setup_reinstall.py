"""A folder whose owned files are gone must accept a new installation."""
from __future__ import annotations
import json
import pytest

from odessy.setup.maintenance import ManagedInstallation, digest
from odessy.setup.contracts import SetupSelection
from odessy.setup.package_context import context
from test_setup_maintenance import bundle, fake_runner


def _payload(tmp_path):
    payload = tmp_path / 'payload'
    payload.mkdir()
    files = {}
    for name in ('desktop/app.exe', 'wheelhouse/manifest.json'):
        path = payload / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'component')
        files[name] = digest(path)
    (payload / 'setup-context.json').write_text(json.dumps(
        {'schema_version': 1, 'components': ['desktop'], 'files': files}))
    return payload


def test_an_uninstalled_folder_is_offered_as_a_new_installation(tmp_path):
    source = bundle(tmp_path)
    payload = _payload(tmp_path)
    manager = ManagedInstallation(tmp_path / 'App', runner=fake_runner,
                                  payload=payload)
    selection = SetupSelection(role='combined')
    plan = manager.plan(source, selection)
    manager.install(source, selection, plan['plan_id'])
    assert context(payload=payload, root=manager.root)['installation_format'] == 'versioned'
    manager.uninstall(confirm=manager.identity())
    assert manager.record()['active'] is None
    user_file = manager.root / 'versions' / 'user-notes.txt'
    user_file.parent.mkdir(exist_ok=True)
    user_file.write_text('keep me')
    assert context(payload=payload, root=manager.root)['installation_format'] == 'new'
    replan = manager.plan(source, selection)
    manager.install(source, selection, replan['plan_id'])
    assert user_file.read_text() == 'keep me'
    assert context(payload=payload, root=manager.root)['installation_format'] == 'versioned'


def test_modified_owned_files_remain_managed(tmp_path):
    source = bundle(tmp_path)
    payload = _payload(tmp_path)
    manager = ManagedInstallation(tmp_path / 'App', runner=fake_runner, payload=payload)
    selection = SetupSelection(role='combined')
    manager.install(source, selection, manager.plan(source, selection)['plan_id'])
    record = manager.record()
    version = record['active']
    name = next(iter(record['versions'][version]))
    changed = manager.root / 'versions' / version / name
    changed.write_bytes(b'user-modified')
    manager.uninstall(confirm=manager.identity())
    assert changed.read_bytes() == b'user-modified'
    assert context(payload=payload, root=manager.root)['installation_format'] == 'versioned'


def test_payload_change_invalidates_review(tmp_path):
    source = bundle(tmp_path)
    payload = _payload(tmp_path)
    manager = ManagedInstallation(tmp_path / 'App', runner=fake_runner, payload=payload)
    selection = SetupSelection(role='combined')
    reviewed = manager.plan(source, selection)['plan_id']
    changed = payload / 'desktop' / 'app.exe'
    changed.write_bytes(b'new component')
    manifest_path = payload / 'setup-context.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files']['desktop/app.exe'] = digest(changed)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='review the exact'):
        manager.install(source, selection, reviewed)
    assert not (manager.root / 'installation.json').exists()


def test_retained_runtime_collision_is_reported_without_deletion(tmp_path):
    import hashlib
    from odessy.setup.package_context import _check_retained_runtime
    from odessy.setup.runtime import inventory
    payload = tmp_path / 'payload'
    runtime = payload / 'runtime'
    (runtime / 'bin').mkdir(parents=True)
    (runtime / 'bin' / 'python3').write_bytes(b'python')
    (runtime / 'LICENSE').write_bytes(b'license')
    expected = inventory(runtime)
    key = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()[:32]
    retained = tmp_path / 'App' / 'runtimes' / key / 'bin' / 'python3'
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b'changed')
    with pytest.raises(ValueError, match='Back up and move'):
        _check_retained_runtime(tmp_path / 'App', payload)
    assert retained.read_bytes() == b'changed'


def test_directory_at_expected_runtime_file_is_rejected(tmp_path):
    import hashlib
    from odessy.setup.package_context import _check_retained_runtime
    from odessy.setup.runtime import inventory
    payload = tmp_path / 'payload'
    runtime = payload / 'runtime'
    (runtime / 'bin').mkdir(parents=True)
    (runtime / 'bin' / 'python3').write_bytes(b'python')
    (runtime / 'LICENSE').write_bytes(b'license')
    key = hashlib.sha256(json.dumps(inventory(runtime), sort_keys=True).encode()).hexdigest()[:32]
    retained = tmp_path / 'App' / 'runtimes' / key / 'bin' / 'python3'
    retained.mkdir(parents=True)
    with pytest.raises(ValueError, match='Back up and move'):
        _check_retained_runtime(tmp_path / 'App', payload)
    assert retained.is_dir()


def test_reinstall_context_rejects_runtime_directory_before_writes(tmp_path):
    import hashlib
    from odessy.setup.runtime import inventory
    payload = _payload(tmp_path)
    runtime = payload / 'runtime'
    (runtime / 'bin').mkdir(parents=True)
    (runtime / 'bin' / 'python3').write_bytes(b'python')
    (runtime / 'LICENSE').write_bytes(b'license')
    manifest_path = payload / 'setup-context.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['components'].append('python')
    for name in ('runtime/bin/python3', 'runtime/LICENSE'):
        manifest['files'][name] = digest(payload / name)
    manifest_path.write_text(json.dumps(manifest))
    root = tmp_path / 'App'
    root.mkdir()
    record_path = root / 'installation.json'
    record = {'schema_version': 1, 'id': 'a' * 32, 'root': str(root),
              'active': None, 'versions': {}}
    record_path.write_text(json.dumps(record))
    key = hashlib.sha256(json.dumps(inventory(runtime), sort_keys=True).encode()).hexdigest()[:32]
    retained = root / 'runtimes' / key / 'bin' / 'python3'
    retained.mkdir(parents=True)
    before = record_path.read_bytes()
    with pytest.raises(ValueError, match='Back up and move'):
        context(payload=payload, root=root)
    assert record_path.read_bytes() == before
    assert retained.is_dir()
    assert not (root / 'operation.json').exists()


def test_missing_runtime_files_are_reusable(tmp_path):
    import hashlib
    from odessy.setup.package_context import _check_retained_runtime
    from odessy.setup.runtime import inventory
    payload = tmp_path / 'payload'
    runtime = payload / 'runtime'
    (runtime / 'bin').mkdir(parents=True)
    (runtime / 'bin' / 'python3').write_bytes(b'python')
    (runtime / 'LICENSE').write_bytes(b'license')
    key = hashlib.sha256(json.dumps(inventory(runtime), sort_keys=True).encode()).hexdigest()[:32]
    (tmp_path / 'App' / 'runtimes' / key / 'bin').mkdir(parents=True)
    _check_retained_runtime(tmp_path / 'App', payload)
