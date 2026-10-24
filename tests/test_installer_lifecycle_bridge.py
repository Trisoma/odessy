import json
from pathlib import Path
import pytest
from installer.core.checkpoint import Step, run_steps, read_state, fingerprint


def legacy(root):
    plan={'install_dir':str(root),'cache_dir':str(root.parent/'cache'),'engine':'role-aware-existing-installer-v1','selection':{'role':'execution'}}
    run_steps(root,plan,[Step('application',lambda:{},lambda:{})])
    app=root/'application';app.mkdir();(app/'odessy-application.json').write_text('{"schema_version":1}');(app/'old.txt').write_text('old')
    (root/'models').mkdir();(root/'models/q8').write_bytes(b'external-model')
    (root/'config').mkdir();(root/'config/odessy.env').write_text('user config')
    (root/'note.txt').write_text('unrelated')
    return root


def quiet(monkeypatch):
    from installer.operations import maintenance
    monkeypatch.setattr(maintenance,'running_processes',lambda root:[])


def test_dispatch_identifies_both_formats_and_rejects_ambiguity(tmp_path,monkeypatch):
    from odessy.setup.lifecycle import dispatch
    from odessy.setup.maintenance import atomic
    quiet(monkeypatch);root=legacy(tmp_path/'legacy')
    assert dispatch({'action':'inspect','root':str(root)})['installation_format']=='legacy'
    atomic(root/'installation.json',{'schema_version':1})
    with pytest.raises(ValueError,match='ambiguous'):dispatch({'action':'inspect','root':str(root)})


def test_real_legacy_uninstall_rejects_changed_plan_and_preserves_data(tmp_path,monkeypatch):
    from odessy.setup.lifecycle import dispatch
    quiet(monkeypatch);root=legacy(tmp_path/'legacy')
    preview=dispatch({'action':'uninstall-plan','root':str(root)})
    assert preview['keep_models'] and preview['keep_config']
    (root/'application/old.txt').write_text('changed')
    with pytest.raises(ValueError):dispatch({'action':'uninstall','root':str(root),'confirm':preview['plan_id']})
    preview=dispatch({'action':'uninstall-plan','root':str(root)})
    result=dispatch({'action':'uninstall','root':str(root),'confirm':preview['plan_id']})
    assert result['status']=='completed' and not (root/'application').exists()
    assert (root/'models/q8').read_bytes()==b'external-model'
    assert (root/'config/odessy.env').read_text()=='user config' and (root/'note.txt').read_text()=='unrelated'


def test_rollback_consent_binds_journal_changes(tmp_path,monkeypatch):
    from odessy.setup.lifecycle import dispatch
    from installer.operations import maintenance
    quiet(monkeypatch);root=legacy(tmp_path/'legacy')
    state=read_state(root)
    record={'schema_version':1,'install_dir':str(root),'id':'a'*32,'status':'preparing','old_state':state,'old_state_sha256':fingerprint(state)}
    maintenance._save(root,record)
    preview=dispatch({'action':'rollback-plan','root':str(root)})
    record['error']='changed';maintenance._save(root,record)
    with pytest.raises(ValueError):dispatch({'action':'rollback','root':str(root),'confirm':preview['plan_id']})
    preview=dispatch({'action':'rollback-plan','root':str(root)})
    assert dispatch({'action':'rollback','root':str(root),'confirm':preview['plan_id']})['status']=='rolled_back'


def test_core_only_activation_and_failure_reuse_existing_journal(tmp_path):
    from installer.operations import maintenance
    from installer.windows.shortcuts import digest
    root=legacy(tmp_path/'legacy');old=read_state(root);bundle=tmp_path/'bundle';bundle.mkdir()
    def planner(root,bundle,exe,**kwargs):
        maintenance.require_idle(root)
        value={'install_dir':str(root),'new_plan':old['plan'],'inventory':maintenance._tree(root,root/'application'),'activation_components':['application'],'scope':'role-aware-core','tool_archive_sha256':'x','executable_sha256':None}
        value['plan_sha256']=fingerprint(value);return value
    def assemble(runtime,deps,bundle,target):
        target.mkdir();(target/'new.txt').write_text('new');return {'python':'unused'}
    preview=planner(root,bundle,None)
    result=maintenance.execute(root,bundle,None,preview['plan_sha256'],planner=planner,assemble=assemble,verify=lambda *args:{'core_verified':True},final_check=lambda app:None,probe=lambda root:[])
    assert result['status']=='completed' and (root/'application/new.txt').exists()
    maintenance.recover(root,probe=lambda root:[])
    assert (root/'application/old.txt').read_text()=='old'


def test_uninstall_cancellation_records_partial_state_and_explicit_resume(tmp_path,monkeypatch):
    from odessy.setup.lifecycle import dispatch
    quiet(monkeypatch);root=legacy(tmp_path/'legacy');count=[0]
    def cancelled():count[0]+=1;return count[0]>=3
    preview=dispatch({'action':'uninstall-plan','root':str(root)})
    with pytest.raises(KeyboardInterrupt):dispatch({'action':'uninstall','root':str(root),'confirm':preview['plan_id']},cancelled=cancelled)
    assert (root/'.installer/uninstall.json').exists()
    preview=dispatch({'action':'uninstall-plan','root':str(root)})
    assert dispatch({'action':'uninstall','root':str(root),'confirm':preview['plan_id']})['status']=='completed'


def test_headless_cli_uses_same_format_dispatch(tmp_path,monkeypatch,capsys):
    from odessy.setup.manage import main
    quiet(monkeypatch);root=legacy(tmp_path/'legacy')
    assert main(['inspect','--root',str(root)])==0
    assert json.loads(capsys.readouterr().out)['installation_format']=='legacy'
    assert main(['uninstall-plan','--root',str(root)])==0
    token=json.loads(capsys.readouterr().out)['plan_id']
    assert main(['uninstall','--root',str(root),'--confirm',token])==0
    assert json.loads(capsys.readouterr().out)['status']=='completed'


def test_versioned_plan_binds_record_and_selected_operation(tmp_path):
    from odessy.setup.lifecycle import dispatch
    from odessy.setup.maintenance import ManagedInstallation,atomic
    root=tmp_path/'versioned';root.mkdir();manager=ManagedInstallation(root)
    record={'schema_version':1,'id':'b'*32,'root':str(root),'active':None,'versions':{}}
    atomic(root/'installation.json',record)
    preview=dispatch({'action':'uninstall-plan','root':str(root)})
    record['selection']={'role':'execution'};atomic(root/'installation.json',record)
    with pytest.raises(ValueError):dispatch({'action':'uninstall','root':str(root),'confirm':preview['plan_id']})
    fresh=dispatch({'action':'uninstall-plan','root':str(root)})
    assert dispatch({'action':'uninstall','root':str(root),'confirm':fresh['plan_id']})['installation_format']=='versioned'


def test_versioned_rollback_rechecks_journal_under_lock(tmp_path):
    from odessy.setup.maintenance import ManagedInstallation,atomic
    root=tmp_path/'native';root.mkdir();manager=ManagedInstallation(root)
    record={'schema_version':1,'id':'b'*32,'root':str(root),'active':None,'versions':{}}
    atomic(root/'installation.json',record)
    atomic(root/'operation.json',{'status':'interrupted'})
    with pytest.raises(ValueError,match='operation journal changed'):
        manager.rollback('c'*32,confirm=record['id'],expected_state=record,expected_operation=None)


def test_core_health_and_artifacts_never_probe_model(tmp_path,monkeypatch):
    from installer.operations import component_repair,role_maintenance
    from installer import workflow
    monkeypatch.setattr(workflow,'steps_for',lambda plan:[Step('runtime',lambda:{},lambda:{}),Step('dependencies',lambda:{},lambda:{})])
    monkeypatch.setattr(component_repair,'dependency_integrity',lambda root:{})
    monkeypatch.setattr(component_repair.model,'load_release',lambda:(_ for _ in ()).throw(AssertionError('model loaded')))
    assert role_maintenance.health(tmp_path,{'engine':'role-aware-existing-installer-v1'})==([],{})
    assert component_repair._artifacts({'new_plan':{'engine':'role-aware-existing-installer-v1'},'repair_components':[]})==[]


def test_versioned_cancelled_uninstall_persists_journal_and_stales_consent(tmp_path):
    import hashlib
    from odessy.setup.lifecycle import dispatch
    from odessy.setup.maintenance import atomic
    root=tmp_path/'native';base=root/'versions'/('c'*32);base.mkdir(parents=True)
    (base/'one').write_bytes(b'one');(base/'two').write_bytes(b'two')
    record={'schema_version':1,'id':'b'*32,'root':str(root),'active':'c'*32,'versions':{'c'*32:{'one':hashlib.sha256(b'one').hexdigest(),'two':hashlib.sha256(b'two').hexdigest()}}}
    atomic(root/'installation.json',record)
    preview=dispatch({'action':'uninstall-plan','root':str(root)});count=[0]
    def cancelled():count[0]+=1;return count[0]>=3
    with pytest.raises(KeyboardInterrupt):dispatch({'action':'uninstall','root':str(root),'confirm':preview['plan_id']},cancelled=cancelled)
    assert json.loads((root/'operation.json').read_text())['status']=='interrupted'
    with pytest.raises(ValueError):dispatch({'action':'uninstall','root':str(root),'confirm':preview['plan_id']})
    fresh=dispatch({'action':'uninstall-plan','root':str(root)})
    assert dispatch({'action':'uninstall','root':str(root),'confirm':fresh['plan_id']})['installation_format']=='versioned'
