import asyncio
import copy
import json
import pytest
from odessy.setup.contracts import SetupSelection

def selection(name='A'):
    return SetupSelection.from_dict({'role':'combined','model':{'provider':'ollama','name':name}})

def test_section_fingerprints_are_canonical_separate_and_secret_free(tmp_path, monkeypatch):
    from odessy.application.capabilities import configuration_descriptor
    monkeypatch.setenv('ODESSY_CLOUD_API_KEY','never-return-this-secret')
    a=configuration_descriptor(selection(), tmp_path, owner='owner')
    b=configuration_descriptor(selection('B'), tmp_path, owner='owner')
    assert a['fingerprints']['llm'] != b['fingerprints']['llm']
    assert a['fingerprints']['tts'] == b['fingerprints']['tts']
    assert a['fingerprints']['host'] == b['fingerprints']['host']
    assert 'never-return' not in json.dumps(a)
    assert a['model']['name']=='A'
    other=configuration_descriptor(selection(), tmp_path, owner='replacement-owner')
    assert a['fingerprint'] != other['fingerprint']

def test_reinstalled_root_has_a_different_configuration_owner(tmp_path):
    from odessy.application.capabilities import configuration_owner
    assert configuration_owner(tmp_path,'a'*32)!=configuration_owner(tmp_path,'b'*32)

def test_capabilities_capture_startup_not_mutable_saved_settings(tmp_path):
    from odessy.application.capabilities import configuration_descriptor
    from odessy.application.api import create_app
    httpx=pytest.importorskip('httpx')
    async def run():
        startup=configuration_descriptor(selection(),tmp_path,owner='owner')
        expected=copy.deepcopy(startup)
        app=create_app(lambda: None, token='secret', runtime_configuration=startup)
        startup['model']['name']='B'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            assert (await client.get('/v1/capabilities')).status_code==401
            response=await client.get('/v1/capabilities',headers={'Authorization':'Bearer secret'})
            data=response.json()
            assert data['configuration']==expected
            assert data['restart_safe'] is True
            app.state.sessions['active']=type('Session',(),{'running':True,'jobs':None})()
            assert (await client.get('/v1/capabilities',headers={'Authorization':'Bearer secret'})).json()['restart_safe'] is False
    asyncio.run(run())

def test_manual_server_configuration_is_unknown_not_inferred():
    from odessy.application.api import create_app
    httpx=pytest.importorskip('httpx')
    async def run():
        app=create_app(lambda: None,token='secret')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            data=(await client.get('/v1/capabilities',headers={'Authorization':'Bearer secret'})).json()
            assert data['configuration'] is None
    asyncio.run(run())

def test_restart_reservation_blocks_new_work_until_explicit_resume():
    from odessy.application.api import create_app
    from odessy.application.session import SessionController
    httpx=pytest.importorskip('httpx')
    async def run():
        app=create_app(lambda:SessionController(model_factory=lambda _:object(),agent_factory=lambda *a,**k:object(),profiles={'local':{}}),token='secret')
        headers={'Authorization':'Bearer secret'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers=headers) as client:
            sid=(await client.post('/v1/sessions')).json()['id']
            instance=(await client.get('/v1/capabilities')).json()['service_instance_id']
            prepared=await client.post('/v1/lifecycle/prepare-restart',json={'service_instance_id':instance})
            assert prepared.status_code==200
            assert (await client.post(f'/v1/sessions/{sid}/turns',json={'text':'hello'})).status_code==409
            assert (await client.post('/v1/lifecycle/resume',json={'ticket':'wrong'})).status_code==409
            assert (await client.post('/v1/lifecycle/resume',json={'ticket':prepared.json()['ticket']})).status_code==200
    asyncio.run(run())

def test_runtime_jobs_prevent_restart_reservation():
    from odessy.application.api import create_app
    httpx=pytest.importorskip('httpx')
    async def run():
        jobs=type('Jobs',(),{'has_active_work':True})()
        app=create_app(lambda:None,token='secret',jobs=jobs)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer secret'}) as client:
            data=(await client.get('/v1/capabilities')).json()
            assert data['restart_safe'] is False
            assert (await client.post('/v1/lifecycle/prepare-restart',json={'service_instance_id':data['service_instance_id']})).status_code==409
    asyncio.run(run())

