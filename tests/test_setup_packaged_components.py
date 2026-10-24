import json
import pytest
from odessy.setup.maintenance import ManagedInstallation, digest
from odessy.setup.contracts import SetupSelection
from test_setup_maintenance import bundle, fake_runner


def test_payload_components_owned_and_execution_excludes_desktop(tmp_path):
    source=bundle(tmp_path);payload=tmp_path/'payload';payload.mkdir()
    files={}
    for name in ('desktop/app.exe','node/node.exe','tools/main.js','link/odessy-link.exe'):
        path=payload/name;path.parent.mkdir(exist_ok=True);path.write_bytes(b'component');files[name]=digest(path)
    (payload/'setup-context.json').write_text(json.dumps({'schema_version':1,'components':['desktop','node','tools','link'],'files':files}))
    manager=ManagedInstallation(tmp_path/'installed',runner=fake_runner,payload=payload)
    selection=SetupSelection(role='execution');plan=manager.plan(source,selection)
    manager.install(source,selection,plan['plan_id'])
    owned=manager.record()['versions'][manager.record()['active']]
    assert 'node/node.exe' in owned and 'tools/main.js' in owned and 'link/odessy-link.exe' in owned
    assert not any(name.startswith('desktop/') for name in owned)
    assert not (manager.root/'versions'/manager.record()['active']/'runtime.env').exists()
    (payload/'node/node.exe').write_bytes(b'changed')
    with pytest.raises(ValueError,match='checksum'):manager.plan(source,selection)


def test_incomplete_bundle_is_rejected_and_absent_components_are_reported(tmp_path):
    source=bundle(tmp_path);payload=tmp_path/'payload';payload.mkdir()
    path=payload/'desktop/app.exe';path.parent.mkdir();path.write_bytes(b'component')
    manager=ManagedInstallation(tmp_path/'installed',runner=fake_runner,payload=payload)
    selection=SetupSelection(role='combined')
    # A declared component with no files is a broken bundle, not an optional gap.
    (payload/'setup-context.json').write_text(json.dumps({'schema_version':1,'components':['desktop','node'],'files':{'desktop/app.exe':digest(path)}}))
    with pytest.raises(ValueError,match='no files'):manager.plan(source,selection)
    # A required desktop application cannot be absent.
    (payload/'setup-context.json').write_text(json.dumps({'schema_version':1,'components':[],'files':{'desktop/app.exe':digest(path)}}))
    with pytest.raises(ValueError,match='required components'):manager.plan(source,selection)
    assert not (manager.root/'versions').exists()
    # The optional Node tool server is reported rather than invented.
    (payload/'setup-context.json').write_text(json.dumps({'schema_version':1,'components':['desktop'],'files':{'desktop/app.exe':digest(path)}}))
    assert manager.plan(source,selection)['absent_components']==['node','tools']


def test_install_journal_reports_real_stages(tmp_path):
    source=bundle(tmp_path);root=tmp_path/'installed';seen=[]
    def runner(argv,**kwargs):
        fake_runner(argv,**kwargs)
        operation=root/'operation.json'
        if operation.exists():seen.append(json.loads(operation.read_text()).get('stage'))
    manager=ManagedInstallation(root,runner=runner)
    selection=SetupSelection(role='execution')
    manager.install(source,selection,manager.plan(source,selection)['plan_id'])
    assert {'environment','dependencies'}.issubset(set(seen))
    status=manager.status()
    assert status['status']=='completed' and status['stage']=='activated'
    assert status['steps_total']>=1 and status['files_written']>=1
    assert status['active']==manager.record()['active']
    assert status['installation_format']=='versioned'


def test_configuration_change_is_reviewed_and_keeps_owned_hashes_valid(tmp_path):
    source=bundle(tmp_path);manager=ManagedInstallation(tmp_path/'installed',runner=fake_runner)
    role=SetupSelection(role='combined')
    manager.install(source,role,manager.plan(source,role)['plan_id'])
    selection=SetupSelection.from_dict({'role':'combined','model':{'provider':'ollama','name':'llama3'}})
    plan=manager.configuration_plan(selection)
    with pytest.raises(ValueError,match='review'):manager.configure(selection,None,'wrong')
    with pytest.raises(ValueError,match='outside the installation'):
        manager.configure(selection,str(manager.root/'workspace'),plan['plan_id'])
    manager.configure(selection,None,plan['plan_id'])
    assert manager.verify()['valid']
    configuration=json.loads((manager.root/'configuration.json').read_text())
    assert configuration['selection']['model']['name']=='llama3'
    assert manager.record()['selection']['model']['name']=='llama3'
    assert manager.status()['configuration']['model_configured'] is True
