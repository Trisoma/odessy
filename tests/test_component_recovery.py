import json
import os
import subprocess
import sys
import time
from pathlib import Path
import pytest
from odessy.setup import component_settings as settings, configured

@pytest.mark.parametrize('after_pointer',[False,True])
def test_killed_writer_requires_reviewed_recovery_and_retains_files(tmp_path,monkeypatch,after_pointer):
    state=tmp_path/'desktop.json'
    monkeypatch.setattr(configured,'desktop_state_file',lambda:state)
    monkeypatch.setattr(configured.Path,'home',lambda:tmp_path)
    ready=tmp_path/'ready'
    script=r'''
import sys,time
from pathlib import Path
from odessy.setup import configured, component_settings as settings
state=Path(sys.argv[1]);ready=Path(sys.argv[2]);after=sys.argv[3]=='True'
configured.desktop_state_file=lambda:state
configured.Path.home=lambda:state.parent
original=configured.remember
def pause(spec):
    if after: original(spec)
    ready.write_text('ready')
    time.sleep(60)
configured.remember=pause
loaded=settings.dispatch({'action':'load'})
data={'section':'tts','patch':{'tts_provider':'custom_audio:factory'},'revision':loaded['revision']}
review=settings.dispatch({'action':'review',**data})
settings.dispatch({'action':'save','confirm':review['plan_id'],**data})
'''
    bootstrap='import sys;sys.path[:0]='+repr(sys.path)+';exec('+repr(script)+')'
    child=subprocess.Popen([sys.executable,'-c',bootstrap,str(state),str(ready),str(after_pointer)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for _ in range(120):
            if ready.exists():break
            if child.poll() is not None:break
            time.sleep(.05)
        assert ready.exists(), child.communicate(timeout=2)
        child.kill();child.wait(timeout=5)
        files=set(tmp_path.glob('components-*.json'))
        assert files
        inspection=settings.dispatch({'action':'lock-inspect'})
        assert inspection['recoverable'] is True
        loaded=settings.dispatch({'action':'load'})
        assert loaded['writer']['pending'] is True
        assert (loaded['selection']['voice']['tts_provider']=='custom_audio:factory') is after_pointer
        with pytest.raises(ValueError,match='Inspect'):
            with settings._source_lock(): pass
        with pytest.raises(ValueError,match='review'):
            settings.dispatch({'action':'lock-recover','confirm':'wrong'})
        settings.dispatch({'action':'lock-recover','confirm':inspection['plan_id']})
        assert files.issubset(set(tmp_path.glob('components-*.json')))
        assert settings.dispatch({'action':'lock-inspect'})['recoverable'] is False
        with settings._source_lock(): pass
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)

def test_unowned_legacy_lock_is_never_removed(tmp_path,monkeypatch):
    monkeypatch.setattr(configured,'desktop_state_file',lambda:tmp_path/'desktop.json')
    path=tmp_path/'.component-settings.lock';path.write_bytes(b'')
    observed=settings.dispatch({'action':'lock-inspect'})
    assert observed['recoverable'] is False
    with pytest.raises(ValueError):settings.dispatch({'action':'lock-recover','confirm':'anything'})
    assert path.read_bytes()==b''
