import pytest
from odessy.setup.native_bridge import manage
from odessy.setup.maintenance import ManagedInstallation, atomic


@pytest.fixture
def root(tmp_path, monkeypatch):
    from odessy.setup import windows_installation
    monkeypatch.setattr(windows_installation, 'refresh', lambda *args: None)
    versions = {}
    for version in ('a' * 32, 'b' * 32):
        base = tmp_path / 'versions' / version
        base.mkdir(parents=True)
        atomic(base / 'selection.json', {'schema_version': 1, 'selection': {'role': 'combined'}})
        versions[version] = {}
    atomic(tmp_path / 'installation.json', {'schema_version': 1, 'id': 'c' * 32,
        'root': str(tmp_path), 'active': 'a' * 32, 'versions': versions,
        'selection': {'role': 'combined'}})
    return tmp_path


def request(root, action, **extra):
    return manage({'action': action, 'root': str(root), 'version': 'b' * 32, **extra})


@pytest.mark.parametrize('confirmation', [None, 'WRONG'])
def test_packaged_rollback_rejects_unreviewed_confirmation(root, confirmation):
    with pytest.raises(ValueError):
        request(root, 'package-rollback', confirm=confirmation)
    assert ManagedInstallation(root).record()['active'] == 'a' * 32


def test_packaged_rollback_rejects_changed_journal(root):
    preview = request(root, 'package-rollback-review')
    atomic(root / 'operation.json', {'status': 'interrupted'})
    with pytest.raises(ValueError):
        request(root, 'package-rollback', confirm=preview['plan_id'])
    assert ManagedInstallation(root).record()['active'] == 'a' * 32


def test_packaged_rollback_applies_matching_review(root):
    preview = request(root, 'package-rollback-review')
    request(root, 'package-rollback', confirm=preview['plan_id'])
    assert ManagedInstallation(root).record()['active'] == 'b' * 32


def test_packaged_rollback_rejects_changed_configuration_state(root):
    preview = request(root, 'package-rollback-review')
    record = ManagedInstallation(root).record()
    record['selection'] = {'role': 'coordinator'}
    atomic(root / 'installation.json', record)
    with pytest.raises(ValueError):
        request(root, 'package-rollback', confirm=preview['plan_id'])
    assert ManagedInstallation(root).record()['active'] == 'a' * 32
