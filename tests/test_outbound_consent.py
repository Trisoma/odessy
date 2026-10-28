from pathlib import Path
import pytest
from odessy.execution import outbound

@pytest.fixture
def config(monkeypatch, tmp_path):
    path=tmp_path/'execution-consent.json'
    monkeypatch.setattr(outbound,'config_path',lambda:path)
    from odessy.setup import configured
    monkeypatch.setattr(configured,'desktop_state_file',lambda:tmp_path/'desktop.json')
    return path

def peer(**changes):
    return dict(ok=True,configured_alias_present=True,revoked=False,
        fingerprint_sha256='ab'*32,grant_revision=4,**changes)

def test_legacy_inbound_grants_never_create_outbound_consent(config):
    assert outbound.read()['enabled'] is False
    assert not config.exists()
    with pytest.raises(PermissionError,match='consent'):
        outbound.authorize('laptop','ab'*32)

def test_exact_review_saves_and_revisions_invalidate_existing_grants(config,tmp_path):
    binary=tmp_path/'link';binary.write_bytes(b'fake')
    reviewed=outbound.review('laptop',True,binary,prober=lambda host:peer())
    assert not config.exists()
    with pytest.raises(ValueError):
        outbound.save('laptop',True,binary,reviewed['revision'],'wrong',prober=lambda host:peer())
    saved=outbound.save('laptop',True,binary,reviewed['revision'],reviewed['plan_id'],prober=lambda host:peer())
    assert outbound.authorize('laptop','ab'*32)==saved['revision']
    with pytest.raises(PermissionError):outbound.authorize('laptop','cd'*32)
    with pytest.raises(PermissionError):outbound.authorize('other','ab'*32)
    disabled=outbound.review('laptop',False,binary,prober=lambda host:peer())
    outbound.save('laptop',False,binary,disabled['revision'],disabled['plan_id'],prober=lambda host:peer())
    assert outbound.read()['alias']=='laptop'
    with pytest.raises(PermissionError):outbound.authorize('laptop','ab'*32)

def test_registry_change_invalidates_review_without_writing(config,tmp_path):
    binary=tmp_path/'link';binary.write_bytes(b'fake')
    reviewed=outbound.review('laptop',True,binary,prober=lambda host:peer())
    with pytest.raises(ValueError):
        outbound.save('laptop',True,binary,reviewed['revision'],reviewed['plan_id'],prober=lambda host:{**peer(),'grant_revision':5})
    assert not config.exists()

def test_revocation_and_unreadable_state_cannot_enable_execution(config,tmp_path):
    binary=tmp_path/'link';binary.write_bytes(b'fake')
    for details in [dict(ok=False),{**peer(),'revoked':True},{**peer(),'configured_alias_present':False}]:
        with pytest.raises(ValueError):outbound.review('laptop',True,binary,prober=lambda host:details)
    config.write_text('{broken')
    with pytest.raises(ValueError):outbound.read()
    assert config.read_text()=='{broken'

def test_unknown_or_forged_owner_is_not_replaced(config):
    config.write_text('{"owner":"someone-else","schema_version":1}')
    with pytest.raises(ValueError):outbound.read()


def test_paired_dispatch_requires_outbound_consent_not_inbound_mcp(config,tmp_path):
    from odessy.tools.paired import paired_host, PairedConfigError, PairedTrustError
    binary=tmp_path/'link';binary.write_bytes(b'fake')
    env={'ODESSY_PAIRED_HOST':'laptop','ODESSY_LINK_BIN':str(binary)}
    details={**peer(),'capability_mcp':True,'workspace':'incoming-only'}
    with pytest.raises(PairedConfigError,match='outbound'):
        paired_host(env,prober=lambda host:details)
    reviewed=outbound.review('laptop',True,binary,prober=lambda host:details)
    saved=outbound.save('laptop',True,binary,reviewed['revision'],reviewed['plan_id'],prober=lambda host:details)
    host=paired_host(env,prober=lambda host:details)
    assert host.server_config().target_metadata['outbound_consent_revision']==saved['revision']
    server=host.server()
    object.__setattr__(server,'prober',lambda host:{**details,'capability_mcp':False})
    server.preflight()  # Incoming access to this computer is not required.
    reviewed=outbound.review('laptop',False,binary)
    outbound.save('laptop',False,binary,reviewed['revision'],reviewed['plan_id'])
    with pytest.raises(PairedTrustError,match='consent'):server.preflight()


def test_disabled_selection_never_becomes_local_execution(config,tmp_path):
    binary=tmp_path/'link';binary.write_bytes(b'fake')
    reviewed=outbound.review('laptop',True,binary,prober=lambda host:peer())
    outbound.save('laptop',True,binary,reviewed['revision'],reviewed['plan_id'],prober=lambda host:peer())
    reviewed=outbound.review('laptop',False,None)
    outbound.save('laptop',False,None,reviewed['revision'],reviewed['plan_id'])
    env={'ODESSY_DEPLOYMENT':'single','ODESSY_TOOL_SERVER_URL':'old-url'}
    outbound.apply_environment(env)
    assert env=={'ODESSY_DEPLOYMENT':'split','ODESSY_PAIRED_HOST':'laptop'}
    config.write_text('{bad')
    env={}
    outbound.apply_environment(env)
    assert env['ODESSY_PAIRED_HOST']=='consent-unavailable'


def test_same_value_resave_changes_revision_and_stale_review_cannot_replay(config,tmp_path):
    binary=tmp_path/'link';binary.write_bytes(b'fake')
    reviewed=outbound.review('laptop',True,binary,prober=lambda host:peer())
    first=outbound.save('laptop',True,binary,reviewed['revision'],reviewed['plan_id'],prober=lambda host:peer())
    second_review=outbound.review('laptop',True,binary,prober=lambda host:peer())
    second=outbound.save('laptop',True,binary,second_review['revision'],second_review['plan_id'],prober=lambda host:peer())
    assert first['revision']!=second['revision']
    with pytest.raises(ValueError):outbound.save('laptop',True,binary,reviewed['revision'],reviewed['plan_id'],prober=lambda host:peer())


def test_fixed_bridge_review_never_sends_an_inbound_grant(config,tmp_path):
    import json
    from odessy.setup.native_bridge import pair
    binary=tmp_path/'link';binary.write_bytes(b'fake')
    calls=[]
    def runner(argv):
        calls.append(argv)
        return json.dumps({'grant_revision':4,'peers':[{'alias':'laptop',**peer()}]})
    args={'alias':'laptop','enabled':True,'link':str(binary)}
    reviewed=pair(dict(args,action='outbound-review'),runner=runner)
    saved=pair(dict(args,action='outbound-save',revision=reviewed['revision'],confirm=reviewed['plan_id']),runner=runner)
    assert saved['enabled'] is True
    assert all(call[1:]==['peers','list','--json'] for call in calls)
