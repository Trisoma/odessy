import io
import json
from pathlib import Path
import pytest
from odessy.setup.bridge import run


def request(operation,parameters):
    output=io.BytesIO()
    run(io.BytesIO((json.dumps({'id':'native-1','operation':operation,'parameters':parameters})+'\n').encode()),output)
    return json.loads(output.getvalue())


def test_management_bridge_rejects_arbitrary_commands():
    result=request('manage',{'action':'shell','root':'/tmp/test','argv':['rm','-rf','/']})
    assert result['status']=='error'


def test_management_bridge_inspect_matches_owned_record(tmp_path):
    from odessy.setup.maintenance import ManagedInstallation,atomic
    root=tmp_path/'runtime';root.mkdir()
    atomic(root/'installation.json',{'schema_version':1,'id':'a'*32,'root':str(root),'active':None,'versions':{}})
    result=request('manage',{'action':'inspect','root':str(root)})
    assert result['status']=='ok'
    assert result['result']['installation_id']=='a'*32


def test_pairing_requires_review_and_binds_certificate_bytes(tmp_path):
    from odessy.setup.native_bridge import pair
    executable=tmp_path/'odessy-link.exe';executable.write_bytes(b'executable')
    certificate=tmp_path/'peer.json';certificate.write_text('{}')
    params={'action':'enroll','link':str(executable),'certificate':str(certificate),'alias':'laptop','fingerprint':'a'*64}
    plan=pair({**params,'review':True},runner=lambda argv:'{"peers":[],"grant_revision":1}')
    assert plan['grants_changed'] is False
    certificate.write_text('{"changed":true}')
    with pytest.raises(ValueError): pair({**params,'confirm':plan['plan_id']},runner=lambda argv:'{"peers":[],"grant_revision":1}')


def test_pairing_grant_is_explicit_and_never_inferred(tmp_path):
    from odessy.setup.native_bridge import pair
    executable=tmp_path/'odessy-link';executable.write_bytes(b'executable')
    with pytest.raises(ValueError): pair({'action':'grant','link':str(executable),'alias':'laptop','review':True})
