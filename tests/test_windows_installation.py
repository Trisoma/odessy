import json
import subprocess
import sys
from pathlib import Path

from odessy.setup import windows_installation as w
import pytest


def installation(root, versions=('a' * 32,)):
    records = {}
    for version in versions:
        base = root / 'versions' / version
        (base / 'desktop').mkdir(parents=True)
        (base / 'desktop' / 'odessy_desktop.exe').write_bytes(('exe-' + version).encode())
        (base / 'selection.json').write_text(json.dumps({'schema_version': 1, 'selection': {'role': 'combined'}}))
        records[version] = {path.relative_to(base).as_posix(): w.digest(path) for path in base.rglob('*') if path.is_file()}
    record = {'schema_version': 1, 'id': 'b' * 32, 'root': str(root), 'active': versions[0],
              'versions': records, 'selection': {'role': 'combined'}}
    (root / 'installation.json').write_text(json.dumps(record))
    return record


@pytest.mark.skipif(w.os.name!='nt',reason='Windows registration guard')
def test_identity_validated_before_helper_copy(tmp_path):
    with pytest.raises(ValueError,match='identity'):
        w.register(tmp_path,{'root':str(tmp_path),'id':'../escape'},payload=tmp_path/'missing')

def test_launch_active(tmp_path, monkeypatch):
    root=tmp_path
    (root/'installation.json').write_text(json.dumps({'active':'a'*32,'versions':{'a'*32:{}},'root':str(root),'id':'b'*32,'schema_version':1}))
    exe=root/'versions'/('a'*32)/'desktop'/'odessy_desktop.exe'
    exe.parent.mkdir(parents=True);exe.write_bytes(b'exe')
    calls=[]
    monkeypatch.setattr(w.subprocess,'Popen',lambda argv,**kw:calls.append((argv,kw)))
    w.launch(root,'manage')
    assert calls[0][0]==[str(exe),'--manage','--install-root',str(root)]
    assert calls[0][1]['cwd']!=str(exe.parent)
    w.launch(root)
    assert calls[1][0]==[str(exe),'--installed','--install-root',str(root)]


def test_installed_launch_does_not_inherit_installer_python_environment(tmp_path, monkeypatch):
    installation(tmp_path)
    calls=[]
    monkeypatch.setenv('PYTHONPATH', str(tmp_path/'bootstrap'))
    monkeypatch.setenv('PYTHONHOME', str(tmp_path/'runtime'))
    monkeypatch.setenv('ODESSY_SETUP_PAYLOAD', str(tmp_path/'payload'))
    monkeypatch.setenv('ODESSY_TEST_KEEP', 'preserved')
    monkeypatch.setattr(w.subprocess, 'Popen', lambda argv, **kwargs: calls.append(kwargs))
    w.launch(tmp_path)
    environment = calls[0]['env']
    assert 'PYTHONPATH' not in environment
    assert 'PYTHONHOME' not in environment
    assert 'ODESSY_SETUP_PAYLOAD' not in environment
    assert environment['ODESSY_TEST_KEEP'] == 'preserved'


@pytest.mark.skipif(w.os.name!='nt',reason='Windows registration guard')
def test_registration_targets_active_version_and_records_complete_state(tmp_path, monkeypatch):
    root=tmp_path/'App';record=installation(root);written={}
    monkeypatch.setattr(w,'_shortcut_path',lambda identity:tmp_path/('Odessy-'+identity+'.lnk'))
    def shortcut(path,target,arguments):
        written.update(shortcut=str(path),target=target,arguments=arguments)
        Path(path).write_text('shortcut')
    monkeypatch.setattr(w,'_write_shortcut',shortcut)
    monkeypatch.setattr(w,'_registry_owner',lambda key:None)
    monkeypatch.setattr(w,'_write_registry',lambda key,values:written.update(key=key,values=values))
    resources=w.register(root,record)
    assert resources['state']=='complete'
    assert written['target']==str(root/'versions'/('a'*32)/'desktop'/'odessy_desktop.exe')
    assert '--installed' in written['arguments'] and '--manage' not in written['arguments']
    assert written['values']['InstallLocation']==str(root)
    assert '-Uninstall' in written['values']['UninstallString']
    assert '-Manage' in written['values']['ModifyPath']
    assert record['windows_registration']['state']=='complete'
    assert resources['files'][str(root/'launch.ps1')]


@pytest.mark.skipif(w.os.name!='nt',reason='Windows registration guard')
def test_registry_failure_leaves_reachable_partial_state(tmp_path, monkeypatch):
    root=tmp_path/'App';record=installation(root);shortcuts=tmp_path/'shortcuts';shortcuts.mkdir()
    monkeypatch.setattr(w,'_shortcut_path',lambda identity:shortcuts/('Odessy-'+identity+'.lnk'))
    written={}
    def shortcut(path,target,arguments):
        written.update(target=target);Path(path).write_text('shortcut')
    monkeypatch.setattr(w,'_write_shortcut',shortcut)
    monkeypatch.setattr(w,'_registry_owner',lambda key:None)
    def fail(key,values): raise OSError('access denied')
    monkeypatch.setattr(w,'_write_registry',fail)
    with pytest.raises(RuntimeError,match='retry'):
        w.register(root,record)
    assert record['windows_registration']['state']=='partial'
    assert (shortcuts/('Odessy-'+'b'*32+'.lnk')).is_file()
    assert written['target'].endswith('odessy_desktop.exe')


@pytest.mark.skipif(w.os.name!='nt',reason='Windows registration guard')
def test_refresh_follows_the_activated_version(tmp_path, monkeypatch):
    root=tmp_path/'App';record=installation(root,versions=('a'*32,'c'*32));shortcut=tmp_path/'Odessy.lnk'
    monkeypatch.setattr(w,'_shortcut_path',lambda identity:shortcut)
    targets=[]
    def write(path,target,arguments):
        targets.append(target);Path(path).write_text('shortcut')
    monkeypatch.setattr(w,'_write_shortcut',write)
    monkeypatch.setattr(w,'_registry_owner',lambda key:None)
    monkeypatch.setattr(w,'_write_registry',lambda key,values:None)
    w.register(root,record)
    record['active']='c'*32
    w.refresh(root,record)
    assert Path(targets[0]).parent.parent.name==('a'*32)
    assert Path(targets[1]).parent.parent.name==('c'*32)
    shortcut.write_text('edited by the user')
    with pytest.raises(ValueError,match='modified externally'):
        w.refresh(root,record)


@pytest.mark.skipif(w.os.name!='nt',reason='Windows registration guard')
def test_helper_cleanup_never_targets_an_unrelated_directory(tmp_path):
    keeper=tmp_path/'not-the-cache';keeper.mkdir();(keeper/'file').write_text('keep')
    result=w.helper_cleanup({'windows_registration':{'helper':str(keeper)}})
    assert result['state']=='retained' and (keeper/'file').is_file()
    assert w.helper_cleanup({'windows_registration':{}})['state']=='none'


@pytest.fixture
def helper_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    path=tmp_path/'OdessyMaintenance'/('b'*32)/('c'*32)
    path.mkdir(parents=True)
    (path/'owned').write_text('owned')
    record={'id':'b'*32,'windows_registration':{'helper':str(path),
        'helper_files':{'owned':w.digest(path/'owned')}}}
    return path,record


@pytest.mark.skipif(w.os.name!='nt',reason='Windows helper cleanup')
def test_helper_cleanup_preserves_modified_and_unowned_files(helper_cache):
    path,record=helper_cache
    (path/'owned').write_text('changed')
    (path/'personal').write_text('keep')
    assert w.helper_cleanup(record)['state']=='retained'
    assert (path/'owned').read_text()=='changed'
    assert (path/'personal').read_text()=='keep'


@pytest.mark.skipif(w.os.name!='nt',reason='Windows helper cleanup')
def test_helper_cleanup_removes_only_matching_owned_files(helper_cache):
    path,record=helper_cache
    (path/'personal').write_text('keep')
    result=w.helper_cleanup(record)
    assert not (path/'owned').exists()
    assert (path/'personal').read_text()=='keep'
    assert result['state']=='retained'


@pytest.mark.skipif(w.os.name!='nt',reason='Windows helper cleanup')
def test_running_helper_is_retained_without_scheduling_deletion(helper_cache,monkeypatch):
    path,record=helper_cache
    monkeypatch.setattr(w.sys,'executable',str(path/'owned'))
    monkeypatch.setattr(w.subprocess,'Popen',lambda *a,**k:pytest.fail('must not schedule deletion'))
    assert w.helper_cleanup(record)['state']=='retained'
    assert (path/'owned').exists()


@pytest.mark.skipif(w.os.name!='nt',reason='Windows helper cleanup')
def test_old_helper_without_inventory_is_retained(helper_cache):
    path,record=helper_cache
    del record['windows_registration']['helper_files']
    assert w.helper_cleanup(record)['state']=='retained'
    assert (path/'owned').exists()


@pytest.mark.skipif(w.os.name!='nt',reason='Windows helper cleanup')
def test_helper_cleanup_removes_fully_owned_cache(helper_cache):
    path,record=helper_cache
    assert w.helper_cleanup(record)['state']=='removed'
    assert not path.exists()


@pytest.mark.skipif(w.os.name!='nt',reason='Windows helper cleanup')
def test_helper_cleanup_rejects_invalid_inventory_before_any_deletion(helper_cache):
    path,record=helper_cache
    record['windows_registration']['helper_files']['../outside']='a'*64
    assert w.helper_cleanup(record)['state']=='retained'
    assert (path/'owned').exists()


@pytest.mark.skipif(w.os.name!='nt',reason='Windows helper cleanup')
def test_helper_cleanup_rejects_another_installations_cache(helper_cache):
    path,record=helper_cache
    record['id']='a'*32
    assert w.helper_cleanup(record)['state']=='retained'
    assert (path/'owned').exists()


@pytest.mark.skipif(w.os.name != 'nt', reason='Windows helper cleanup')
def test_uninstall_stays_running_until_maintenance_cache_is_removed(helper_cache, monkeypatch):
    from odessy.setup.lifecycle import dispatch
    helper, cache_record = helper_cache
    root = helper.parents[2] / 'installed app'
    record = installation(root)
    record['windows_registration'] = cache_record['windows_registration']
    w.atomic(root/'installation.json', record)
    original_digest = w.digest
    observations = []

    def inspect_progress(path):
        if path == helper/'owned':
            journal = json.loads((root/'operation.json').read_text())
            observations.append(journal)
            assert journal['status'] == 'running'
            assert journal['stage'] == 'maintenance-cache'
            assert journal['files_done'] < journal['files_total']
        return original_digest(path)

    monkeypatch.setattr(w, 'digest', inspect_progress)
    preview = dispatch({'action': 'uninstall-plan', 'root': str(root)})
    result = dispatch({'action': 'uninstall', 'root': str(root), 'confirm': preview['plan_id']})
    assert observations
    assert result['helper_cleanup']['state'] == 'removed'
    journal = json.loads((root/'operation.json').read_text())
    assert journal['status'] == 'completed'
    assert journal['files_done'] == journal['files_total']


@pytest.mark.skipif(w.os.name != 'nt', reason='Windows helper cleanup')
def test_uninstall_can_cancel_cache_cleanup_and_resume_after_fresh_review(helper_cache):
    from odessy.setup.lifecycle import dispatch
    helper, cache_record = helper_cache
    root = helper.parents[2] / 'installed app'
    record = installation(root)
    record['windows_registration'] = cache_record['windows_registration']
    w.atomic(root/'installation.json', record)

    def cancel_during_cache():
        path = root/'operation.json'
        return path.exists() and json.loads(path.read_text()).get('stage') == 'maintenance-cache'

    preview = dispatch({'action': 'uninstall-plan', 'root': str(root)})
    with pytest.raises(KeyboardInterrupt):
        dispatch({'action': 'uninstall', 'root': str(root), 'confirm': preview['plan_id']},
                 cancelled=cancel_during_cache)
    assert (helper/'owned').is_file()
    assert json.loads((root/'operation.json').read_text())['status'] == 'interrupted'
    with pytest.raises(ValueError):
        dispatch({'action': 'uninstall', 'root': str(root), 'confirm': preview['plan_id']})
    fresh = dispatch({'action': 'uninstall-plan', 'root': str(root)})
    result = dispatch({'action': 'uninstall', 'root': str(root), 'confirm': fresh['plan_id']})
    assert result['helper_cleanup']['state'] == 'removed'


@pytest.mark.skipif(w.os.name != 'nt', reason='Windows helper cleanup')
def test_helper_cleanup_overlaps_checksum_reads_and_preserves_changed_content(helper_cache, monkeypatch):
    import threading
    helper, record = helper_cache
    (helper/'second').write_bytes(b'second')
    record['windows_registration']['helper_files']['second'] = w.digest(helper/'second')
    # The installed digest belongs to the old contents, so this file must survive.
    (helper/'owned').write_text('user modification')
    original_digest = w.digest
    readers = threading.Barrier(2)
    checked = set()
    lock = threading.Lock()

    def concurrent_digest(path):
        with lock:
            checked.add(path.name)
        readers.wait(timeout=3)
        return original_digest(path)

    monkeypatch.setattr(w, 'digest', concurrent_digest)
    result = w.helper_cleanup(record)
    assert checked == {'owned', 'second'}
    assert result['state'] == 'retained'
    assert (helper/'owned').read_text() == 'user modification'
    assert not (helper/'second').exists()


@pytest.mark.skipif(w.os.name != 'nt', reason='Windows helper cleanup')
def test_cache_cancellation_joins_only_the_current_bounded_batch(helper_cache, monkeypatch):
    import threading
    helper, record = helper_cache
    inventory = record['windows_registration']['helper_files']
    for number in range(11):
        member = helper / f'file-{number}'
        member.write_bytes(b'owned')
        inventory[member.name] = inventory['owned']
    original_digest = w.digest
    lock = threading.Lock()
    checked = []

    def track_read(path):
        value = original_digest(path)
        with lock:
            checked.append(path)
        return value

    monkeypatch.setattr(w, 'digest', track_read)
    with pytest.raises(KeyboardInterrupt):
        w.helper_cleanup(record, cancelled=lambda: len(checked) >= 4)
    assert len(checked) == 4
    assert sum(member.is_file() for member in helper.iterdir()) == 8
    # All worker threads have joined before cancellation returns to the caller.
    result = w.helper_cleanup(record)
    assert result['state'] == 'removed'


@pytest.mark.skipif(w.os.name!='nt',reason='Windows registration')
def test_registration_records_helper_ownership_inventory(tmp_path,helper_cache,monkeypatch):
    helper,_=helper_cache
    (helper/'desktop').mkdir()
    (helper/'desktop/odessy_desktop.exe').write_bytes(b'helper')
    manifest={'schema_version':1,'components':['desktop'],
              'files':{'desktop/odessy_desktop.exe':w.digest(helper/'desktop/odessy_desktop.exe')}}
    (helper/'setup-context.json').write_text(json.dumps(manifest))
    root=tmp_path/'installation';root.mkdir()
    record=installation(root)
    shortcut=tmp_path/'owned.lnk'
    monkeypatch.setattr(w,'_cache_helper',lambda *args,**kwargs:helper)
    monkeypatch.setattr(w,'_shortcut_path',lambda *args:shortcut)
    monkeypatch.setattr(w,'_write_shortcut',lambda *args:shortcut.write_bytes(b'shortcut'))
    monkeypatch.setattr(w,'_registry_owner',lambda *args:None)
    monkeypatch.setattr(w,'_write_registry',lambda *args:None)
    resources=w.register(root,record,payload=helper)
    assert resources['helper_files']==dict(manifest['files'],**{
        'setup-context.json':w.digest(helper/'setup-context.json')})


@pytest.mark.skipif(w.os.name!='nt',reason='Windows registration')
def test_uninstall_intent_does_not_rewrite_installation_path(tmp_path,monkeypatch):
    root=tmp_path/'folder--manage';root.mkdir()
    record=installation(root)
    shortcut=tmp_path/'shortcut.lnk'
    monkeypatch.setattr(w,'_shortcut_path',lambda *args:shortcut)
    monkeypatch.setattr(w,'_write_shortcut',lambda *args:shortcut.write_bytes(b'shortcut'))
    monkeypatch.setattr(w,'_registry_owner',lambda *args:None)
    monkeypatch.setattr(w,'_write_registry',lambda *args:None)
    values=w.register(root,record)['registry_values']
    assert str(root/'launch.ps1') in values['UninstallString']
    assert '-Uninstall' in values['UninstallString']


@pytest.mark.skipif(w.os.name!='nt',reason='Windows subprocess flags')
def test_shortcut_helper_does_not_open_a_console(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(w.subprocess,'run',lambda *args,**kwargs:calls.append(kwargs))
    w._write_shortcut(tmp_path/'shortcut.lnk','target.exe','--installed')
    assert calls[0]['creationflags'] & w.subprocess.CREATE_NO_WINDOW
def test_launch_helper_closes_output_while_desktop_remains_alive(tmp_path):
    """A real grandchild must not hold the helper's protocol pipes open."""
    code = '''
import sys, subprocess
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from odessy.setup import windows_installation as w
w.ManagedInstallation = lambda root: type('Manager', (), {'record': lambda self: {}})()
w._active_desktop = lambda root, record: ('a'*32, Path(sys.executable))
real_popen = subprocess.Popen
def child(args, **kwargs):
    return real_popen([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
w.subprocess.Popen = child
p = w.launch(Path(sys.argv[2]))
print(p.pid, flush=True)
'''
    source = str(Path(__file__).resolve().parents[1] / 'src')
    helper = subprocess.Popen([sys.executable, '-c', code, source, str(tmp_path)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pid = None
    try:
        # Read one protocol line, then require EOF without closing the desktop.
        pid = int(helper.stdout.readline().strip())
        try:
            out, err = helper.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            pytest.fail('desktop inherited helper output pipes; setup cannot finish its launch request')
        assert helper.returncode == 0, err.decode(errors='replace')
        assert out == b''
    finally:
        if pid is not None:
            if sys.platform == 'win32':
                subprocess.run(['taskkill.exe', '/PID', str(pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                import os, signal
                os.kill(pid, signal.SIGTERM)
        if helper.poll() is None: helper.kill()
        helper.communicate()

def _payload(tmp_path, files):
    """A minimal reviewed payload: a real manifest plus the declared members."""
    payload = tmp_path / 'payload'
    payload.mkdir(exist_ok=True)
    manifest = {'schema_version': 1, 'components': ['desktop'], 'files': {}}
    for name, content in files.items():
        member = payload / name
        member.parent.mkdir(parents=True, exist_ok=True)
        member.write_bytes(content)
        manifest['files'][name] = w.digest(member)
    (payload / 'setup-context.json').write_text(json.dumps(manifest))
    return payload, manifest


@pytest.mark.skipif(w.os.name != 'nt', reason='Windows maintenance cache')
def test_a_completed_copy_is_reused_without_rewriting_any_file(tmp_path, monkeypatch):
    root = tmp_path / 'App'
    record = installation(root)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    payload, manifest = _payload(tmp_path, {'desktop/odessy_desktop.exe': b'exe',
                                            'desktop/data.json': b'data'})
    helper = w._cache_helper(root, record, payload)
    stamps = {helper / name: (helper / name).stat().st_mtime_ns for name in manifest['files']}
    # A second attempt verifies the cache and copies nothing.
    assert w._cache_helper(root, record, payload) == helper
    for path, stamp in stamps.items():
        assert path.stat().st_mtime_ns == stamp
    assert sorted(manifest['files']) == ['desktop/data.json', 'desktop/odessy_desktop.exe']


@pytest.mark.skipif(w.os.name != 'nt', reason='Windows maintenance cache')
def test_an_interrupted_copy_resumes_and_replaces_a_torn_file(tmp_path, monkeypatch):
    root = tmp_path / 'App'
    record = installation(root)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    payload, manifest = _payload(tmp_path, {'desktop/odessy_desktop.exe': b'exe-' * 100,
                                            'desktop/data.json': b'data'})
    helper = w._cache_helper(root, record, payload)
    kept = helper / 'desktop' / 'odessy_desktop.exe'
    torn = helper / 'desktop' / 'data.json'
    before = kept.stat().st_mtime_ns
    torn.write_bytes(b'torn by an interrupted copy')
    (helper / 'desktop' / 'stale.part').write_bytes(b'leftover')
    w._cache_helper(root, record, payload)
    assert torn.read_bytes() == b'data'
    assert kept.stat().st_mtime_ns == before          # verified entries are not rewritten
    assert w.digest(kept) == manifest['files']['desktop/odessy_desktop.exe']


@pytest.mark.skipif(w.os.name != 'nt', reason='Windows maintenance cache')
def test_cancelling_the_copy_records_it_and_owns_nothing(tmp_path, monkeypatch):
    root = tmp_path / 'App'
    record = installation(root)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    payload, _ = _payload(tmp_path, {'desktop/odessy_desktop.exe': b'exe',
                                     'desktop/data.json': b'data'})
    journal_path = root / w.REGISTRATION_JOURNAL
    monkeypatch.setattr(w, '_shortcut_path', lambda identity: tmp_path / 'shortcut.lnk')
    monkeypatch.setattr(w, '_write_shortcut', lambda *a: pytest.fail('cancelled registration wrote a shortcut'))
    monkeypatch.setattr(w, '_write_registry', lambda *a: pytest.fail('cancelled registration wrote a registry entry'))
    with pytest.raises(w.Cancelled):
        w.register(root, record, payload=payload, cancelled=lambda: True)
    assert 'windows_registration' not in record
    assert not journal_path.exists() or json.loads(journal_path.read_text())['status'] == 'cancelled'
    assert not (tmp_path / 'local' / 'OdessyMaintenance').exists()


@pytest.mark.skipif(w.os.name != 'nt', reason='Windows maintenance cache')
def test_a_failed_copy_is_recorded_as_interrupted_without_false_success(tmp_path, monkeypatch):
    root = tmp_path / 'App'
    record = installation(root)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    payload, _ = _payload(tmp_path, {'desktop/odessy_desktop.exe': b'exe'})
    monkeypatch.setattr(w.shutil, 'copyfile',
                        lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    monkeypatch.setattr(w, '_shortcut_path', lambda identity: tmp_path / 'shortcut.lnk')
    with pytest.raises(OSError, match='disk full'):
        w.register(root, record, payload=payload)
    journal = json.loads((root / w.REGISTRATION_JOURNAL).read_text())
    assert journal['status'] == 'interrupted' and 'disk full' in journal['error']
    assert 'windows_registration' not in record


@pytest.mark.skipif(w.os.name != 'nt', reason='Windows maintenance cache')
def test_registration_progress_is_bounded_and_reported(tmp_path, monkeypatch):
    root = tmp_path / 'App'
    record = installation(root)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    payload, _ = _payload(tmp_path, {'desktop/odessy_desktop.exe': b'exe' * 10,
                                     'desktop/data.json': b'data'})
    monkeypatch.setattr(w, '_shortcut_path', lambda identity: tmp_path / 'shortcut.lnk')
    monkeypatch.setattr(w, '_write_shortcut', lambda path, target, arguments: Path(path).write_text('shortcut'))
    monkeypatch.setattr(w, '_registry_owner', lambda key: None)
    monkeypatch.setattr(w, '_write_registry', lambda key, values: None)
    w.register(root, record, payload=payload)
    journal = json.loads((root / w.REGISTRATION_JOURNAL).read_text())
    assert journal['status'] == 'complete' and journal['stage'] == 'registered'
    assert journal['files_done'] == journal['files_total'] == 2
    assert journal['bytes_done'] == journal['bytes_total'] == 34
    assert set(journal) <= {'schema_version', 'operation', 'status', 'stage', 'installation_id',
                            'version', 'files_done', 'files_total', 'bytes_done', 'bytes_total',
                            'reused', 'replaced', 'started_at', 'updated_at', 'finished_at',
                            'logs', 'log_cursor', 'stage_started_at', 'error'}
    shown = w.registration_progress(root)
    assert shown['status'] == 'complete' and 'logs' not in shown
