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
    monkeypatch.setattr(w,'_cache_helper',lambda *args:helper)
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
