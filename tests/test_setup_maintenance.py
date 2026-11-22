import hashlib
import json
from pathlib import Path
import pytest
from odessy.setup.maintenance import ManagedInstallation, Cancelled
from odessy.setup.contracts import SetupSelection


def test_accumulated_upgrade_metadata_remains_readable(tmp_path):
    from odessy.setup.maintenance import read_json
    path=tmp_path/'installation.json'
    # Repeated full Windows upgrades retain rollback inventories and registration
    # hashes; their combined metadata can legitimately exceed the former 16 MiB.
    payload='x'*(17*1024*1024)
    path.write_text(json.dumps({'rollback_metadata':payload}),encoding='utf-8')
    assert read_json(path)['rollback_metadata']==payload


def test_oversized_atomic_record_preserves_previous_file(tmp_path,monkeypatch):
    from odessy.setup import maintenance
    path=tmp_path/'installation.json';path.write_text('{"previous":true}')
    monkeypatch.setattr(maintenance,'MAX_RECORD_BYTES',64)
    with pytest.raises(ValueError,match='record exceeds limit'):
        maintenance.atomic(path,{'rollback_metadata':'x'*65})
    assert path.read_text()=='{"previous":true}'
    assert list(tmp_path.iterdir())==[path]
    path.write_text(json.dumps({'oversized':'x'*65}))
    with pytest.raises(ValueError,match='record exceeds limit'):
        maintenance.read_json(path)


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


def test_status_exposes_recorded_helper_even_when_executable_is_damaged(tmp_path):
    from odessy.setup.maintenance import atomic
    source=bundle(tmp_path)
    manager=ManagedInstallation(tmp_path/'installation',runner=fake_runner)
    selection=SetupSelection(role='execution')
    manager.install(source,selection,manager.plan(source,selection)['plan_id'])
    helper=tmp_path/'helper';helper.mkdir()
    (helper/'setup-context.json').write_text('{}')
    record=manager.record()
    record['linux_registration']={'state':'complete','helper':str(helper)}
    atomic(manager.root/'installation.json',record)
    active=record['active']
    (manager.root/'versions'/active/'selection.json').write_text('damaged')
    assert manager.status()['payload']==str(helper)


@pytest.mark.parametrize('problem',[ValueError('linked installation path'),OSError('helper is inaccessible')])
def test_status_ignores_unsafe_helper_and_preserves_progress(tmp_path,monkeypatch,problem):
    from odessy.setup import maintenance
    source=bundle(tmp_path)
    manager=ManagedInstallation(tmp_path/'installation',runner=fake_runner)
    selection=SetupSelection(role='execution')
    manager.install(source,selection,manager.plan(source,selection)['plan_id'])
    helper=tmp_path/'unsafe-helper'
    record=manager.record()
    record['linux_registration']={'state':'complete','helper':str(helper)}
    maintenance.atomic(manager.root/'installation.json',record)
    original_guard=maintenance.guard
    def check_guard(path):
        if Path(path)==helper: raise problem
        return original_guard(path)
    monkeypatch.setattr(maintenance,'guard',check_guard)
    status=manager.status()
    assert status['payload'] is None
    assert status['active']==record['active']
    assert status['versions']==list(record['versions'])


def test_plan_is_invalidated_by_runtime_change(tmp_path):
    source=bundle(tmp_path)
    runtime=tmp_path/'runtime'
    (runtime/'bin').mkdir(parents=True)
    executable=runtime/'bin'/'python3'
    executable.write_bytes(b'original')
    (runtime/'LICENSE').write_text('license')
    manager=ManagedInstallation(tmp_path/'installation',runner=fake_runner,runtime_source=runtime)
    selection=SetupSelection(role='execution')
    reviewed=manager.plan(source,selection)['plan_id']
    executable.write_bytes(b'changed')
    with pytest.raises(ValueError,match='review the exact'):
        manager.install(source,selection,reviewed)
    assert not (manager.root/'installation.json').exists()


def test_install_rechecks_plan_inside_lock(tmp_path, monkeypatch):
    source=bundle(tmp_path)
    manager=ManagedInstallation(tmp_path/'installation',runner=fake_runner)
    selection=SetupSelection(role='execution')
    reviewed=manager.plan(source,selection)['plan_id']
    original=manager.plan
    calls=0
    def changed_under_lock(*args):
        nonlocal calls
        calls+=1
        result=original(*args)
        if calls==2: result['plan_id']='changed-after-lock'
        return result
    monkeypatch.setattr(manager,'plan',changed_under_lock)
    with pytest.raises(ValueError,match='installation changed after review'):
        manager.install(source,selection,reviewed)
    assert not (manager.root/'installation.json').exists()


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

@pytest.mark.parametrize('kind', ['symlink', 'reparse'])
def test_guard_checks_each_ancestor_once_and_rejects_links(tmp_path, monkeypatch, kind):
    from odessy.setup import maintenance
    import stat
    from types import SimpleNamespace
    path = tmp_path / 'missing' / 'file'
    calls = []
    original = Path.lstat
    def counted(item):
        calls.append(item)
        return original(item)
    monkeypatch.setattr(Path, 'lstat', counted)
    assert maintenance.guard(path) == path
    assert calls == [path, *path.parents]
    if kind == 'reparse' and maintenance.os.name != 'nt':
        pytest.skip('Windows reparse point attribute')
    def linked(item):
        if item == tmp_path:
            return SimpleNamespace(st_mode=stat.S_IFLNK if kind == 'symlink' else stat.S_IFDIR, st_file_attributes=1024)
        return original(item)
    monkeypatch.setattr(Path, 'lstat', linked)
    with pytest.raises(ValueError, match='linked|reparse'):
        maintenance.guard(path)


def test_uninstall_reports_file_progress_and_preserves_modified_files(tmp_path, monkeypatch):
    from odessy.setup import maintenance
    source = bundle(tmp_path)
    manager = ManagedInstallation(tmp_path/'installation', runner=fake_runner)
    selection = SetupSelection(role='execution')
    manager.install(source, selection, manager.plan(source, selection)['plan_id'])
    changed = manager.root/'versions'/manager.record()['active']/'selection.json'
    changed.write_text('user modification')
    snapshots = []
    original = maintenance.Progress.flush
    def capture(progress, force=False):
        snapshots.append(dict(progress.journal))
        original(progress, force)
    monkeypatch.setattr(maintenance.Progress, 'flush', capture)
    report = manager.uninstall(confirm=manager.identity())
    assert changed.read_text() == 'user modification'
    assert report['retained']
    final = snapshots[-1]
    assert final['files_total'] > 0
    assert final['files_done'] == final['files_total']
    assert any(0 < s.get('files_done', 0) < final['files_total'] for s in snapshots)


def test_voice_only_configuration_preserves_model_and_rejects_concurrent_edit(tmp_path):
    from odessy.setup.native_bridge import manage
    from odessy.setup import maintenance
    from odessy.setup.contracts import ModelSelection
    source = bundle(tmp_path)
    manager = ManagedInstallation(tmp_path/'installed', runner=fake_runner)
    selection = SetupSelection(role='combined', model=ModelSelection(provider='deepseek', name='current-model'))
    manager.install(source, selection, manager.plan(source, selection)['plan_id'])
    patch = {'root': str(manager.root), 'selection': {'voice': {'enabled': False, 'tts_provider': 'piper'}}}
    plan = manage({'action': 'package-voice-configure-review', **patch})
    assert plan['configuration']['selection']['model']['name'] == 'current-model'
    assert manage({'action': 'package-voice-configure', 'confirm': plan['plan_id'], **patch})['configured']
    plan = manage({'action': 'package-voice-configure-review', **patch})
    next_selection = SetupSelection(role='combined', model=ModelSelection(provider='deepseek', name='newer-model'))
    next_plan = manager.configuration_plan(next_selection)
    manager.configure(next_selection, None, next_plan['plan_id'])
    with pytest.raises(ValueError, match='review the exact configuration'):
        manage({'action': 'package-voice-configure', 'confirm': plan['plan_id'], **patch})
    assert maintenance.read_json(manager.root/'configuration.json')['selection']['model']['name'] == 'newer-model'


def test_piper_upgrade_failure_leaves_previous_version_active(tmp_path, monkeypatch):
    from odessy.setup import voice_install
    source = bundle(tmp_path)
    manager = ManagedInstallation(tmp_path/'installed', runner=fake_runner)
    selection = SetupSelection(role='combined')
    manager.install(source, selection, manager.plan(source, selection)['plan_id'])
    old = manager.record()['active']
    def fail(*args, **kwargs):
        raise ValueError('Piper verification failed')
    monkeypatch.setattr(voice_install, 'prepare_piper_upgrade', fail)
    with pytest.raises(ValueError, match='Piper verification failed'):
        manager.repair(source, confirm=manager.plan(source, selection)['plan_id'])
    assert manager.record()['active'] == old
    assert manager.verify()['valid']


def test_voice_patch_preserves_audio_devices_and_allows_explicit_updates(tmp_path):
    from odessy.setup.contracts import VoiceSelection
    source = bundle(tmp_path)
    manager = ManagedInstallation(tmp_path/'installed', runner=fake_runner)
    selection = SetupSelection(role='combined', voice=VoiceSelection(
        audio_input_device='saved microphone', audio_output_device='saved speaker',
        audio_backend='native', wake_model='wake.onnx'))
    manager.install(source, selection, manager.plan(source, selection)['plan_id'])
    patch = {'enabled': False, 'tts_provider': 'piper'}
    plan = manager.voice_configuration_plan(patch)
    manager.configure_voice(patch, plan['plan_id'])
    voice = manager.record()['selection']['voice']
    assert voice['audio_input_device'] == 'saved microphone'
    assert voice['audio_output_device'] == 'saved speaker'
    assert voice['audio_backend'] == 'native' and voice['wake_model'] == 'wake.onnx'
    patch = {'audio_input_device': 'new microphone', 'audio_output_device': ''}
    manager.configure_voice(patch, manager.voice_configuration_plan(patch)['plan_id'])
    assert manager.record()['selection']['voice']['audio_input_device'] == 'new microphone'
    assert manager.record()['selection']['voice']['audio_output_device'] == ''
    for invalid in (None, [], {'unsupported': True}):
        with pytest.raises(ValueError):
            manager.voice_configuration_plan(invalid)


def test_reviewed_speech_pause_recovers_empty_cache_upgrade(tmp_path, monkeypatch):
    import os
    from dataclasses import replace
    from odessy.setup import voice_install
    from odessy.setup.contracts import VoiceSelection
    source = bundle(tmp_path)
    manager = ManagedInstallation(tmp_path/'installed', runner=fake_runner)
    selection = SetupSelection(role='combined')
    manager.install(source, selection, manager.plan(source, selection)['plan_id'])
    old = manager.record()['active']
    old_python = manager.root/'versions'/old/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    selection = replace(selection, voice=VoiceSelection(enabled=True, tts_provider='piper',
        tts_executable=str(old_python), tts_model='saved.onnx', audio_input_device='saved mic'))
    manager.configure(selection, None, manager.configuration_plan(selection)['plan_id'])
    def empty_cache(*args, **kwargs):
        raise ValueError('speech components have not been downloaded yet')
    monkeypatch.setattr(voice_install, 'python_install', empty_cache)
    with pytest.raises(ValueError, match='Pause speech for update'):
        manager.repair(source, confirm=manager.plan(source, selection)['plan_id'])
    assert manager.record()['active'] == old
    assert manager.status()['configuration']['voice_enabled'] is True
    patch = {'enabled': False}
    review = manager.voice_configuration_plan(patch)
    with pytest.raises(ValueError):
        manager.configure_voice(patch, 'not-reviewed')
    manager.configure_voice(patch, review['plan_id'])
    paused = SetupSelection.from_dict(manager.record()['selection'])
    manager.repair(source, confirm=manager.plan(source, paused)['plan_id'])
    assert manager.record()['active'] != old
    assert manager.record()['selection']['voice']['tts_executable'] == str(old_python)
    assert manager.record()['selection']['voice']['audio_input_device'] == 'saved mic'
    assert manager.status()['configuration']['voice_enabled'] is False
    patch = {'enabled': True}
    manager.configure_voice(patch, manager.voice_configuration_plan(patch)['plan_id'])
    assert manager.status()['configuration']['voice_enabled'] is True
    assert manager.verify()['valid']
