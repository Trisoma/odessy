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
    assert plan['arguments'][-4:] == ['--expect-existing', 'absent', '--expect-revision', '1']
    assert '--expect-bundle-sha256' in plan['arguments']
    certificate.write_text('{"changed":true}')
    with pytest.raises(ValueError): pair({**params,'confirm':plan['plan_id']},runner=lambda argv:'{"peers":[],"grant_revision":1}')


def test_pairing_reenrollment_binds_existing_peer_and_version(tmp_path):
    from odessy.setup.native_bridge import pair
    executable=tmp_path/'link'; executable.write_bytes(b'executable')
    certificate=tmp_path/'peer.json'; certificate.write_text('{}')
    params={'action':'enroll','link':str(executable),'certificate':str(certificate),'alias':'laptop','fingerprint':'a'*64}
    state={'revision': 1, 'fingerprint': 'b'*64}; calls=[]
    def runner(argv):
        calls.append(argv[1:])
        if argv[1:] == ['peers','list','--json']:
            return json.dumps({'peers':[{'alias':'laptop','fingerprint_sha256':state['fingerprint']}], 'grant_revision':state['revision']})
        assert argv[-4:] == ['--expect-existing', state['fingerprint'], '--expect-revision', str(state['revision'])]
        return 'enrolled'
    plan=pair({**params,'review':True},runner=runner)
    state['fingerprint']='c'*64
    with pytest.raises(ValueError): pair({**params,'confirm':plan['plan_id']},runner=runner)
    assert all(call==['peers','list','--json'] for call in calls)
    state['fingerprint']='b'*64
    assert pair({**params,'confirm':plan['plan_id']},runner=runner)['grants_changed'] is False


def test_selected_peer_binding_refuses_new_identity_or_revision(tmp_path):
    from odessy.setup.native_bridge import pair
    executable=tmp_path/'link'; executable.write_bytes(b'executable')
    state={'revision': 2, 'fingerprint': 'a'*64}; calls=[]
    def runner(argv):
        calls.append(argv[1:])
        if argv[1:] == ['peers','list','--json']:
            return json.dumps({'peers':[{'alias':'laptop','fingerprint_sha256':state['fingerprint']}], 'grant_revision':state['revision']})
        return 'granted'
    params={'action':'grant-telemetry','link':str(executable),'alias':'laptop',
            'expected_peer_fingerprint':'a'*64,'expected_peer_revision':2}
    assert pair({**params,'review':True},runner=runner)['plan_id']
    state['fingerprint']='b'*64
    with pytest.raises(ValueError): pair({**params,'review':True},runner=runner)
    state['fingerprint']='a'*64; state['revision']=3
    with pytest.raises(ValueError): pair({**params,'review':True},runner=runner)
    assert all(call==['peers','list','--json'] for call in calls)


def test_pairing_grant_is_explicit_and_never_inferred(tmp_path):
    from odessy.setup.native_bridge import pair
    executable=tmp_path/'odessy-link';executable.write_bytes(b'executable')
    with pytest.raises(ValueError): pair({'action':'grant','link':str(executable),'alias':'laptop','review':True})


def test_telemetry_grant_requires_review_and_binds_registry_without_execution_grant(tmp_path):
    from odessy.setup.native_bridge import pair
    executable = tmp_path / 'odessy-link'
    executable.write_bytes(b'executable')
    params = {'action': 'grant-telemetry', 'link': str(executable), 'alias': 'laptop'}
    calls = []
    revision = [1]
    def runner(argv):
        calls.append(argv[1:])
        if argv[1:] == ['peers', 'list', '--json']:
            return json.dumps({'peers': [{'alias': 'laptop', 'fingerprint_sha256': 'a' * 64}], 'grant_revision': revision[0]})
        assert argv[1:] == ['peers', 'grant', 'laptop', '--capability', 'telemetry.read', '--expect-sha256', 'a' * 64, '--expect-revision', str(revision[0])]
        return 'telemetry access granted'
    with pytest.raises(ValueError):
        pair(params, runner=runner)
    assert all(call == ['peers', 'list', '--json'] for call in calls)
    plan = pair({**params, 'review': True}, runner=runner)
    assert plan['arguments'] == ['peers', 'grant', 'laptop', '--capability', 'telemetry.read', '--expect-sha256', 'a' * 64, '--expect-revision', '1']
    assert plan['grants_changed'] is True
    revision[0] = 2
    with pytest.raises(ValueError):
        pair({**params, 'confirm': plan['plan_id']}, runner=runner)
    assert all(call == ['peers', 'list', '--json'] for call in calls)
    revision[0] = 1
    result = pair({**params, 'confirm': plan['plan_id']}, runner=runner)
    assert result['grants_changed'] is True
    assert calls[-1] == ['peers', 'grant', 'laptop', '--capability', 'telemetry.read', '--expect-sha256', 'a' * 64, '--expect-revision', '1']


@pytest.mark.parametrize('action', ['grant', 'revoke'])
def test_reviewed_grant_and_full_revoke_require_current_identity(tmp_path, action):
    from odessy.setup.native_bridge import pair
    executable=tmp_path/'odessy-link'; executable.write_bytes(b'executable')
    params={'action': action, 'link': str(executable), 'alias': 'peer', 'workspace': str(tmp_path)}
    state={'revision': 1, 'fingerprint': 'a'*64}
    calls=[]
    def runner(argv):
        calls.append(argv[1:])
        if argv[1:] == ['peers','list','--json']:
            return json.dumps({'peers':[{'alias':'peer', 'fingerprint_sha256':state['fingerprint']}], 'grant_revision':state['revision']})
        assert argv[-4:] == ['--expect-sha256', state['fingerprint'], '--expect-revision', str(state['revision'])]
        return 'completed'
    plan=pair({**params, 'review': True}, runner=runner)
    state['fingerprint']='b'*64
    with pytest.raises(ValueError): pair({**params, 'confirm':plan['plan_id']},runner=runner)
    assert all(call == ['peers','list','--json'] for call in calls)
    state['fingerprint']='a'*64
    assert pair({**params, 'confirm':plan['plan_id']},runner=runner)['grants_changed'] is True


@pytest.mark.parametrize(('action', 'capability'), [('revoke-telemetry', 'telemetry.read'), ('revoke-execution', 'mcp')])
def test_selective_revoke_binds_review_to_peer_fingerprint_and_revision(tmp_path, action, capability):
    from odessy.setup.native_bridge import pair
    executable = tmp_path / 'odessy-link'; executable.write_bytes(b'executable')
    params = {'action': action, 'link': str(executable), 'alias': 'laptop'}
    state = {'revision': 1, 'fingerprint': 'a' * 64, 'output': 'revoked ' + capability + ' for laptop'}
    calls = []
    def runner(argv):
        calls.append(argv[1:])
        if argv[1:] == ['peers', 'list', '--json']:
            return json.dumps({'peers': [{'alias': 'laptop', 'fingerprint_sha256': state['fingerprint']}],
                               'grant_revision': state['revision']})
        assert argv[1:] == ['peers', 'revoke', 'laptop', '--capability', capability,
                            '--expect-sha256', state['fingerprint'], '--expect-revision', str(state['revision'])]
        return state['output']
    plan = pair({**params, 'review': True}, runner=runner)
    assert plan['arguments'][-4:] == ['--expect-sha256', 'a' * 64, '--expect-revision', '1']
    state['revision'] = 2
    with pytest.raises(ValueError): pair({**params, 'confirm': plan['plan_id']}, runner=runner)
    state['revision'] = 1; state['fingerprint'] = 'b' * 64
    with pytest.raises(ValueError): pair({**params, 'confirm': plan['plan_id']}, runner=runner)
    assert all(call == ['peers', 'list', '--json'] for call in calls)
    state['fingerprint'] = 'a' * 64
    assert pair({**params, 'confirm': plan['plan_id']}, runner=runner)['grants_changed'] is True
    state['output'] = 'already absent ' + capability + ' for laptop'
    assert pair({**params, 'confirm': plan['plan_id']}, runner=runner)['grants_changed'] is False
