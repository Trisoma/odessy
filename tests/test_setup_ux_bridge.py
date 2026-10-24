from unittest.mock import MagicMock
import pytest
from odessy.setup.native_bridge import manage


def test_pending_launches_only_include_current_installation_version(tmp_path):
    from odessy.setup import launch_receipts
    from odessy.setup.maintenance import atomic
    record = {'schema_version': 1, 'root': str(tmp_path), 'id': 'b'*32,
              'active': 'a'*32, 'versions': {'a'*32: {}, 'c'*32: {}}}
    atomic(tmp_path/'installation.json', record)
    launch_receipts.prepare(tmp_path, '1'*32)
    launch_receipts.prepare(tmp_path, '2'*32)
    launch_receipts.acknowledge(tmp_path, '2'*32, 'ready')
    assert manage({'action': 'package-launch-pending', 'root': str(tmp_path)}) == {'launch_ids': ['1'*32]}
    record['active'] = 'c'*32
    atomic(tmp_path/'installation.json', record)
    assert manage({'action': 'package-launch-pending', 'root': str(tmp_path)}) == {'launch_ids': []}


def test_progress_cursor_is_forwarded(tmp_path, monkeypatch):
    from odessy.setup import lifecycle, maintenance
    monkeypatch.setattr(lifecycle, 'format_of', lambda root: 'versioned')
    manager = MagicMock()
    manager.status.return_value = {'logs': [], 'log_cursor': 7}
    monkeypatch.setattr(maintenance, 'ManagedInstallation', lambda root: manager)
    assert manage({'action': 'package-status', 'root': str(tmp_path), 'log_cursor': 5})['log_cursor'] == 7
    manager.status.assert_called_once_with(log_cursor=5)


@pytest.mark.parametrize('cursor', [-1, True, '5'])
def test_progress_cursor_rejects_invalid_values(tmp_path, cursor):
    with pytest.raises(ValueError):
        manage({'action': 'package-status', 'root': str(tmp_path), 'log_cursor': cursor})


def test_launch_receipt_operations_are_fixed_dispatch(tmp_path, monkeypatch):
    from odessy.setup import launch_receipts
    acknowledge=MagicMock(return_value={'state':'ready'})
    status=MagicMock(return_value={'state':'pending'})
    monkeypatch.setattr(launch_receipts,'acknowledge',acknowledge)
    monkeypatch.setattr(launch_receipts,'status',status)
    identifier='a'*32
    assert manage({'action':'package-launch-ack','root':str(tmp_path),'launch_id':identifier,'state':'ready'})['state']=='ready'
    acknowledge.assert_called_once_with(tmp_path,identifier,'ready')
    assert manage({'action':'package-launch-status','root':str(tmp_path),'launch_id':identifier})['state']=='pending'
    status.assert_called_once_with(tmp_path,identifier)


def test_receipt_pruning_preserves_unowned_files(tmp_path):
    import os
    from odessy.setup import launch_receipts
    from odessy.setup.maintenance import atomic
    atomic(tmp_path/'installation.json',{'schema_version':1,'root':str(tmp_path),
        'id':'b'*32,'active':'a'*32,'versions':{'a'*32:{}}})
    directory=tmp_path/'launch-receipts';directory.mkdir()
    note=directory/'notes.json';note.write_text('{"personal":"keep"}')
    os.utime(note,(1,1))
    for index in range(65):launch_receipts.prepare(tmp_path,f'{index:032x}')
    assert note.read_text()=='{"personal":"keep"}'
