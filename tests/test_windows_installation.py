from pathlib import Path
from odessy.setup import windows_installation as w
import pytest

@pytest.mark.skipif(w.os.name!='nt',reason='Windows registration guard')
def test_identity_validated_before_helper_copy(tmp_path):
    with pytest.raises(ValueError,match='identity'):
        w.register(tmp_path,{'root':str(tmp_path),'id':'../escape'},payload=tmp_path/'missing')

def test_launch_active(tmp_path, monkeypatch):
    import json
    root=tmp_path
    (root/'installation.json').write_text(json.dumps({'active':'a'*32,'versions':{'a'*32:{}},'root':str(root),'id':'b'*32,'schema_version':1}))
    exe=root/'versions'/('a'*32)/'desktop'/'odessy_desktop.exe'
    exe.parent.mkdir(parents=True);exe.write_bytes(b'exe')
    calls=[]
    monkeypatch.setattr(w.subprocess,'Popen',lambda argv,**kw:calls.append((argv,kw)))
    w.launch(root,'manage')
    assert calls[0][0]==[str(exe),'--manage','--install-root',str(root)]
    assert calls[0][1]['cwd']!=str(exe.parent)
