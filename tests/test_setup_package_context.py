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
    assert result['root']==str(tmp_path/'local'/'Odessy')
    (payload/'wheelhouse'/'manifest.json').write_text('changed')
    with pytest.raises(ValueError,match='checksum'):context(payload=payload)

def test_context_rejects_escaping_inventory(tmp_path):
    (tmp_path/'setup-context.json').write_text(json.dumps({'schema_version':1,'components':[],'files':{'../outside':'0'*64}}))
    with pytest.raises(ValueError):context(payload=tmp_path)
