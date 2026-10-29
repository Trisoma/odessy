"""Linux desktop integration: owned launcher, menu entry and cache cleanup."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from odessy.setup import linux_installation as linux

pytestmark=pytest.mark.skipif(not sys.platform.startswith('linux'),
                             reason='Linux desktop integration only')

IDENTITY='a'*32
VERSION='b'*32


def _root(tmp_path):
    root=(tmp_path/'install').absolute()
    desktop=root/'versions'/VERSION/'desktop'
    desktop.mkdir(parents=True)
    binary=desktop/linux.DESKTOP_NAME.split('/')[-1]
    binary.write_text('#!/bin/sh\nexit 0\n',encoding='utf-8')
    binary.chmod(0o755)
    record={'schema_version':1,'id':IDENTITY,'root':str(root),'active':VERSION,
            'versions':{VERSION:{'desktop/odessy_desktop':'c'*64}}}
    (root/'installation.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    return root,record


@pytest.fixture
def data_home(tmp_path,monkeypatch):
    home=(tmp_path/'home').absolute()
    home.mkdir()
    monkeypatch.setenv('HOME',str(home))
    monkeypatch.setenv('XDG_DATA_HOME',str(home/'.local/share'))
    return home


def test_register_writes_an_executable_launcher_and_a_menu_entry(tmp_path,data_home):
    root,record=_root(tmp_path)
    resources=linux.register(root,record)
    assert resources['state']=='complete'
    launcher=root/'launch.sh'
    assert launcher.is_file()
    assert os.access(launcher,os.X_OK)
    entry=data_home/'.local/share/applications'/('odessy-'+IDENTITY+'.desktop')
    assert entry.is_file()
    text=entry.read_text(encoding='utf-8')
    assert 'Type=Application' in text
    assert 'Exec='+str(launcher) in text
    assert '[Desktop Action Manage]' in text
    assert '[Desktop Action Uninstall]' in text
    assert record['linux_registration']['menu_entry']==str(entry)


def test_register_rejects_a_launcher_that_was_changed(tmp_path,data_home):
    root,record=_root(tmp_path)
    linux.register(root,record)
    (root/'launch.sh').write_text('#!/bin/sh\necho tampered\n',encoding='utf-8')
    with pytest.raises(ValueError,match='launcher'):
        linux.register(root,record)


def test_launcher_resolves_the_active_version_and_passes_the_mode(tmp_path,data_home):
    root,record=_root(tmp_path)
    linux.register(root,record)
    capture=tmp_path/'args.txt'
    binary=root/'versions'/VERSION/linux.DESKTOP_NAME
    binary.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n',encoding='utf-8')
    binary.chmod(0o755)
    subprocess.run([str(root/'launch.sh'),'--manage','--extra'],
                   check=True,env={**os.environ,'CAPTURE':str(capture)})
    args=capture.read_text(encoding='utf-8').split()
    assert args[:3]==['--manage','--install-root',str(root)]
    assert args[3]=='--extra'


def test_launcher_refuses_a_record_without_an_active_version(tmp_path,data_home):
    root,record=_root(tmp_path)
    linux.register(root,record)
    record['active']=None
    (root/'installation.json').write_text(json.dumps(record),encoding='utf-8')
    result=subprocess.run([str(root/'launch.sh')],capture_output=True,text=True)
    assert result.returncode!=0
    assert 'no active version' in result.stderr


def test_unregister_removes_unchanged_files_and_retains_a_modified_one(tmp_path,data_home):
    root,record=_root(tmp_path)
    linux.register(root,record)
    entry=Path(record['linux_registration']['menu_entry'])
    entry.write_text(entry.read_text(encoding='utf-8')+'\n# edited\n',encoding='utf-8')
    retained=linux.unregister(root,record)
    assert retained==[str(entry)]
    assert entry.is_file()
    assert not (root/'launch.sh').exists()


def test_helper_cleanup_removes_only_the_recorded_cache(tmp_path,data_home):
    root,record=_root(tmp_path)
    helper=(data_home/'.local/share/odessy/maintenance'/IDENTITY/('d'*32))
    (helper/'desktop').mkdir(parents=True)
    payload=helper/'desktop/odessy_desktop'
    payload.write_text('#!/bin/sh\nexit 0\n',encoding='utf-8')
    from odessy.setup.maintenance import digest
    resources=linux.register(root,record)
    resources['helper']=str(helper)
    resources['helper_files']={'desktop/odessy_desktop':digest(payload),
                               'setup-context.json':digest(helper/'desktop/odessy_desktop')}
    (helper/'setup-context.json').write_text('{}',encoding='utf-8')
    resources['helper_files']['setup-context.json']=digest(helper/'setup-context.json')
    result=linux.helper_cleanup(record)
    assert result['state']=='removed'
    assert not helper.exists()
