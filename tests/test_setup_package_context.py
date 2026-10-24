import hashlib,json
import pytest
from odessy.setup.package_context import context

def test_context_verifies_payload_and_never_uses_checkout(tmp_path):
    payload=tmp_path/'payload';payload.mkdir()
    (payload/'wheelhouse').mkdir();(payload/'wheelhouse'/'manifest.json').write_text('{}')
    data=b'{}'
    (payload/'setup-context.json').write_text(json.dumps({'schema_version':1,'components':['python','desktop'],'files':{'wheelhouse/manifest.json':hashlib.sha256(data).hexdigest()}}))
    result=context(payload=payload,local_app_data=tmp_path/'local')
    assert result['verified'] and result['bundle']==str(payload/'wheelhouse')
    assert result['root']==str(tmp_path/'local'/'Odessy'/'App')
    (payload/'wheelhouse'/'manifest.json').write_text('changed')
    with pytest.raises(ValueError,match='checksum'):context(payload=payload)

def test_context_rejects_escaping_inventory(tmp_path):
    (tmp_path/'setup-context.json').write_text(json.dumps({'schema_version':1,'components':[],'files':{'../outside':'0'*64}}))
    with pytest.raises(ValueError):context(payload=tmp_path)

def test_default_root_does_not_collide_with_installer_cache(tmp_path):
    payload=tmp_path/'payload';payload.mkdir()
    (payload/'wheelhouse').mkdir();(payload/'wheelhouse'/'manifest.json').write_text('{}')
    data=b'{}'
    (payload/'setup-context.json').write_text(json.dumps({'schema_version':1,'components':['python','desktop'],'files':{'wheelhouse/manifest.json':hashlib.sha256(data).hexdigest()}}))
    local=tmp_path/'local'
    # The packaged launcher extracts itself under LOCALAPPDATA/Odessy/Setup.
    (local/'Odessy'/'Setup'/'extracted').mkdir(parents=True)
    (local/'Odessy'/'Setup'/'extracted'/'desktop').write_text('installer')
    result=context(payload=payload,local_app_data=local)
    assert result['root']==str(local/'Odessy'/'App')
    assert result['launch_mode']=='setup'

def test_payload_paths_are_audited(tmp_path):
    (tmp_path/'setup-context.json').write_text(json.dumps({'schema_version':1,'components':['desktop'],'files':{}}))
    with pytest.raises(ValueError,match='inventory'):context(payload=tmp_path)
    def write(files):
        (tmp_path/'setup-context.json').write_text(json.dumps({'schema_version':1,'components':['desktop'],'files':files}))
    write({'desktop/a.exe':'0'*64,'DESKTOP/a.exe':'0'*64})
    with pytest.raises(ValueError,match='duplicate'):context(payload=tmp_path)
    write({'unexpected/a.exe':'0'*64})
    with pytest.raises(ValueError,match='unsupported'):context(payload=tmp_path)
    write({'desktop/a.exe':'0'*64})
    with pytest.raises(ValueError,match='incomplete'):context(payload=tmp_path)
