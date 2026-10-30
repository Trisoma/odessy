import hashlib
import json
from pathlib import Path
import pytest
from odessy.setup.maintenance import ManagedInstallation, Cancelled
from odessy.setup.contracts import SetupSelection


def bundle(tmp_path):
    root=tmp_path/'bundle'; root.mkdir()
    wheel=root/'odessy-0.5.0-py3-none-any.whl'; wheel.write_bytes(b'wheel')
    manifest={'schema_version':1,'artifacts':[{'file':wheel.name,'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'license':'Apache-2.0'}]}
    (root/'manifest.json').write_text(json.dumps(manifest))
    return root


def fake_runner(argv, **kwargs):
    if 'venv' in argv:
        target=Path(argv[-1]); (target/'Scripts').mkdir(parents=True); (target/'bin').mkdir()
        (target/'Scripts/python.exe').write_bytes(b'python'); (target/'bin/python').write_bytes(b'python')
    return None


def test_review_consent_no_downloads_and_external_models_preserved(tmp_path):
    source=bundle(tmp_path); root=tmp_path/'installation'; external=tmp_path/'models';external.mkdir();(external/'q8').write_bytes(b'model')
    manager=ManagedInstallation(root,runner=fake_runner)
    selection=SetupSelection(role='execution')
    plan=manager.plan(source,selection)
    assert plan['downloads']==[]
    with pytest.raises(ValueError): manager.install(source,selection,'wrong')
    result=manager.install(source,selection,plan['plan_id'])
    assert result['installed'] and result['service_started'] is False
    assert manager.verify()['valid']
    (root/'user.txt').write_text('keep')
    report=manager.uninstall(confirm=manager.identity())
    assert (external/'q8').read_bytes()==b'model' and (root/'user.txt').read_text()=='keep'
    assert report['removed']
    # The record stays so management can report the uninstall, but the folder is
    # reusable: the next install reuses that record instead of refusing it.
    assert manager.record()['active'] is None
    again=ManagedInstallation(root,runner=fake_runner)
    plan=again.plan(source,SetupSelection(role='execution'))
    again.install(source,SetupSelection(role='execution'),plan['plan_id'])
    assert again.record()['active']


def test_failed_upgrade_preserves_active_installation(tmp_path):
    source=bundle(tmp_path); manager=ManagedInstallation(tmp_path/'installation',runner=fake_runner);selection=SetupSelection(role='combined')
    manager.install(source,selection,manager.plan(source,selection)['plan_id']); old=manager.record()['active']
    def fail(argv, **kwargs):
        if 'pip' in argv: raise RuntimeError('disk full')
        fake_runner(argv,**kwargs)
    manager.runner=fail
    with pytest.raises(RuntimeError): manager.install(source,selection,manager.plan(source,selection)['plan_id'])
    assert manager.record()['active']==old and manager.verify()['valid']


def test_checksum_cancel_and_installation_lock(tmp_path):
    source=bundle(tmp_path);manager=ManagedInstallation(tmp_path/'installation',runner=fake_runner);selection=SetupSelection(role='combined')
    plan=manager.plan(source,selection)
    with pytest.raises(Cancelled): manager.install(source,selection,plan['plan_id'],cancelled=lambda:True)
    with manager.lock():
        with pytest.raises(RuntimeError,match='busy'): manager.install(source,selection,manager.plan(source,selection)['plan_id'])
    next(source.glob('*.whl')).write_bytes(b'bad')
    with pytest.raises(ValueError): manager.plan(source,selection)


def test_repair_preserves_modified_and_external_files(tmp_path):
    source=bundle(tmp_path);manager=ManagedInstallation(tmp_path/'installation',runner=fake_runner);selection=SetupSelection(role='execution')
    manager.install(source,selection,manager.plan(source,selection)['plan_id'])
    active=manager.record()['active']; config=manager.root/'versions'/active/'selection.json';config.write_text('changed')
    assert not manager.verify()['valid']
    repaired=manager.repair(source,confirm=manager.plan(source,selection)['plan_id'])
    assert repaired['installed'] and config.read_text()=='changed'
    manager.uninstall(confirm=manager.identity())
    assert config.read_text()=='changed'


def test_refuses_unowned_root_and_symlink_paths(tmp_path):
    root=tmp_path/'existing';root.mkdir();(root/'unrelated').write_text('keep')
    manager=ManagedInstallation(root,runner=fake_runner)
    with pytest.raises(ValueError): manager.install(bundle(tmp_path),SetupSelection(role='combined'),'invalid')


def test_rollback_restores_selected_versions_role(tmp_path):
    source=bundle(tmp_path);manager=ManagedInstallation(tmp_path/'installation',runner=fake_runner)
    old=SetupSelection(role='execution');new=SetupSelection(role='combined')
    manager.install(source,old,manager.plan(source,old)['plan_id']);old_version=manager.record()['active']
    manager.install(source,new,manager.plan(source,new)['plan_id'])
    manager.rollback(old_version,confirm=manager.identity())
    assert manager.record()['selection']['role']=='execution'


def test_plan_is_invalidated_by_another_installation(tmp_path):
    source=bundle(tmp_path);manager=ManagedInstallation(tmp_path/'installation',runner=fake_runner);selection=SetupSelection(role='execution')
    stale=manager.plan(source,selection)['plan_id']
    manager.install(source,selection,stale)
    with pytest.raises(ValueError): manager.install(source,selection,stale)


def test_uninstall_refuses_while_service_is_recorded(tmp_path):
    source=bundle(tmp_path);manager=ManagedInstallation(tmp_path/'installation',runner=fake_runner);selection=SetupSelection(role='execution')
    manager.install(source,selection,manager.plan(source,selection)['plan_id'])
    (manager.root/'service.json').write_text('{}')
    with pytest.raises(ValueError): manager.uninstall(confirm=manager.identity())
    assert manager.verify()['valid']


def test_upgrade_refuses_to_leave_registered_service_on_old_version(tmp_path):
    source=bundle(tmp_path);manager=ManagedInstallation(tmp_path/'installation',runner=fake_runner);selection=SetupSelection(role='execution')
    manager.install(source,selection,manager.plan(source,selection)['plan_id'])
    (manager.root/'service.json').write_text('{}')
    with pytest.raises(ValueError): manager.repair(source,confirm=manager.plan(source,selection)['plan_id'])
