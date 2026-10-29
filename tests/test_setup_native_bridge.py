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

def test_pairing_telemetry_grant_does_not_grant_execution_and_revision_invalidates(tmp_path):
    from odessy.setup.native_bridge import pair
    executable=tmp_path/'odessy-link';executable.write_bytes(b'executable')
    params={'action':'grant','link':str(executable),'alias':'laptop','capability':'telemetry.read'}
    plan=pair({**params,'review':True},runner=lambda _: '{"peers":[],"grant_revision":1}')
    assert plan['arguments'][-1]=='telemetry.read' and '--workspace' not in plan['arguments']
    with pytest.raises(ValueError,match='review'):
        pair({**params,'confirm':plan['plan_id']},runner=lambda _: '{"peers":[],"grant_revision":2}')


def test_short_code_boundary_cannot_be_enabled_by_caller_flags(tmp_path):
    from odessy.setup.native_bridge import pair
    binary=tmp_path/'link';binary.write_bytes(b'fake')
    commands=[]
    def forged(argv,stdin_text=None):
        commands.append((argv,stdin_text))
        return '{"schema_version":1,"supported":false,"state":"closed","reason":"protocol_review_required"}'
    result=pair({'action':'short-code','command':'join','code':'012345','address':'192.168.1.2:7444','link':str(binary)},runner=forged)
    # The production gate is passed through, never re-interpreted as an approval.
    assert result['supported'] is False and result['reason']=='protocol_review_required'
    # The code never reaches a command line and never appears in the result.
    assert '012345' not in json.dumps(result)
    assert all('012345' not in ' '.join(argv) for argv,_ in commands)
    assert commands[0][1] and '012345' in commands[0][1], 'the code travels on stdin only'


def test_short_code_reply_must_carry_a_session_identity(tmp_path):
    from odessy.setup.native_bridge import pair
    binary=tmp_path/'link';binary.write_bytes(b'fake')
    with pytest.raises(ValueError):
        pair({'action':'short-code','command':'join','code':'012345','address':'192.168.1.2:7444','link':str(binary)},
            runner=lambda argv,stdin_text=None:'{"schema_version":1,"supported":true,"state":"awaiting_approval"}')
    with pytest.raises(ValueError):
        pair({'action':'short-code','command':'status','link':str(binary)},
            runner=lambda argv,stdin_text=None:'{"schema_version":1,"supported":false,"state":"ready"}')


def test_short_code_forwards_a_reviewed_begin_and_hides_the_code(tmp_path):
    from odessy.setup.native_bridge import pair
    binary=tmp_path/'link';binary.write_bytes(b'fake')
    workspace=tmp_path/'workspace';workspace.mkdir()
    seen=[]
    def began(argv,stdin_text=None):
        seen.append(argv)
        return json.dumps({'schema_version':1,'supported':True,'state':'discoverable',
            'session_id':'a'*32,'review_token':'','code':'000042'})
    result=pair({'action':'short-code','command':'begin','workspace':str(workspace),'link':str(binary)},runner=began)
    assert result['state']=='discoverable' and result['code']=='000042'
    assert '--workspace' in seen[0] and str(workspace) in seen[0]
    # Only an opening window shows a code; every other reply is scrubbed.
    scrubbed=pair({'action':'short-code','command':'status','link':str(binary)},
        runner=lambda argv,stdin_text=None: json.dumps({'schema_version':1,'supported':True,
            'state':'discoverable','session_id':'a'*32,'code':'000042'}))
    assert 'code' not in scrubbed


def test_short_code_confirm_requires_the_review_token(tmp_path):
    from odessy.setup.native_bridge import pair
    binary=tmp_path/'link';binary.write_bytes(b'fake')
    with pytest.raises(ValueError,match='stale'):
        pair({'action':'short-code','command':'confirm','session_id':'a'*32,'link':str(binary)},
            runner=lambda argv,stdin_text=None: '{}')
    seen=[]
    pair({'action':'short-code','command':'confirm','session_id':'a'*32,'review_token':'b'*32,'link':str(binary)},
        runner=lambda argv,stdin_text=None: seen.append(argv) or json.dumps({'schema_version':1,
            'supported':True,'state':'paired_no_access','session_id':'a'*32}))
    assert '--review-token' in seen[0] and '--session-id' in seen[0]


def test_short_code_workspace_must_exist(tmp_path):
    from odessy.setup.native_bridge import pair
    binary=tmp_path/'link';binary.write_bytes(b'fake')
    with pytest.raises(ValueError,match='folder'):
        pair({'action':'short-code','command':'begin','workspace':str(tmp_path/'missing'),'link':str(binary)},
            runner=lambda argv,stdin_text=None: '{}')
