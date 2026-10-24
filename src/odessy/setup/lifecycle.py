"""Select the owned installation format; reuse its original maintenance engine."""
from __future__ import annotations
import contextlib
import io
import json
from pathlib import Path
from .maintenance import guard,read_json,ManagedInstallation
from .contracts import fields

ACTIONS={'inspect','verify','repair-plan','repair','rollback-plan','rollback','uninstall-plan','uninstall'}


def format_of(root):
    root=guard(root)
    legacy=guard(root/'.installer/state.json').exists()
    versioned=guard(root/'installation.json').exists()
    if legacy and versioned:raise ValueError('ambiguous installation records; no backend was selected')
    return 'legacy' if legacy else 'versioned' if versioned else None


def _token(action,root,details,parameters):
    from installer.core.checkpoint import fingerprint
    def stable(value):
        if isinstance(value,dict):return {key:stable(item) for key,item in value.items() if key!='free_bytes'}
        if isinstance(value,list):return [stable(item) for item in value]
        return value
    return fingerprint({'operation':action,'root':str(root),'details':stable(details),'arguments':{k:v for k,v in parameters.items() if k not in ('confirm','action')}})


def _without_confirm(parameters):
    return {key:value for key,value in parameters.items() if key not in ('action','confirm')}


def dispatch(parameters,*,cancelled=lambda:False):
    data=fields(parameters,{'action','root','bundle','config','role','confirm','version','workspace','port','node_link','node_server','listen','executable','allow_inference','allow_model_downloads'},'lifecycle')
    if not isinstance(data.get('root'),str) or not data['root'].strip():raise ValueError('explicit installation root required')
    root=guard(data['root']);kind=format_of(root);action=data.get('action')
    if kind is None:return None
    if action not in ACTIONS:
        if kind=='legacy':raise ValueError('this operation is not supported by the legacy installer format')
        return None
    if kind=='legacy':return _legacy(root,action,data,cancelled)
    manager=ManagedInstallation(root)
    if action=='inspect':
        record=manager.record()
        configuration=None
        if (root/'configuration.json').is_file():
            configuration=read_json(root/'configuration.json')
        return {'installation_format':kind,'installation_id':record['id'],'active':record['active'],'versions':list(record['versions']),'selection':(configuration or {}).get('selection') or record.get('selection'),'workspace':(configuration or {}).get('workspace'),'service_recorded':(root/'service.json').exists(),'registration':record.get('windows_registration',{}).get('state','none'),'operation':read_json(root/'operation.json') if (root/'operation.json').exists() else None,'ready':False}
    if action=='verify':return dict(manager.verify(),installation_format=kind)
    if action in ('rollback-plan','uninstall-plan','rollback','uninstall'):
        record=manager.record()
        operation='rollback' if action.startswith('rollback') else 'uninstall'
        if (root/'service.json').exists():raise ValueError('remove the recorded service first')
        if operation=='rollback' and data.get('version') not in record['versions']:raise ValueError('select an existing owned version')
        operation_state=read_json(root/'operation.json') if (root/'operation.json').exists() else None
        details={'record':record,'operation':operation_state,'target_version':data.get('version') if operation=='rollback' else None}
        token=_token(operation,root,details,data)
        if action.endswith('-plan'):return {'installation_format':kind,'operation':operation,'plan_id':token,'installation_id':record['id'],'target_version':details['target_version'],'keep_models':True,'keep_config':True,'ready':False}
        if data.get('confirm')!=token:raise ValueError('review the exact operation and current installation first')
        if cancelled():raise KeyboardInterrupt()
        result=manager.rollback(data['version'],confirm=record['id'],expected_state=record,expected_operation=operation_state) if operation=='rollback' else manager.uninstall(confirm=record['id'],expected_state=record,expected_operation=operation_state,cancelled=cancelled)
        if operation=='rollback':
            # The launcher must follow the version that is actually active.
            from .maintenance import atomic
            from .windows_installation import refresh
            updated=manager.record()
            try:
                refresh(root,updated)
                atomic(root/'installation.json',updated)
            except (ValueError,RuntimeError,OSError) as error:
                result['registration_error']=str(error)
        if operation=='uninstall' and record.get('windows_registration'):
            from .windows_installation import helper_cleanup, unregister
            result['retained_registration']=unregister(root,record)
            result['helper_cleanup']=helper_cleanup(record)
        return dict(result,installation_format=kind)
    return None


def _legacy(root,action,data,cancelled):
    from installer.core.checkpoint import read_state,fingerprint
    from installer.operations import maintenance,component_repair,uninstall,role_maintenance
    state=read_state(root)
    core=state['plan'].get('engine')=='role-aware-existing-installer-v1'
    if action=='inspect':
        journal=maintenance.journal(root)
        removal=read_json(root/uninstall.RECORD) if (root/uninstall.RECORD).exists() else None
        return {'installation_format':'legacy','engine':'installer.workflow','status':state['status'],'summary':state['summary'],'stages':state['steps'],'selection':state['plan'].get('selection'),'maintenance':journal,'uninstall':removal,'ready':False}
    if action=='verify':
        if not core:raise ValueError('legacy pinned model health is reported in the repair plan; no inference is implicit')
        from installer import role_aware
        checks=[]
        for step in role_aware.steps_for(state['plan']):
            try:checks.append({'component':step.name,'valid':step.validate() is not None})
            except (OSError,ValueError):checks.append({'component':step.name,'valid':False})
        return {'installation_format':'legacy','components':checks,'ready':False}
    operation=action.removesuffix('-plan')
    probe=maintenance.running_processes
    if operation=='uninstall':
        preview=uninstall.plan(root,keep_models=True,keep_config=True,probe=probe)
        preview['file_hashes']=role_maintenance.content_inventory(root,preview['inventory'])
        details=preview
    elif operation=='rollback':details=role_maintenance.recovery_snapshot(root)
    elif operation=='repair':
        if not data.get('bundle'):raise ValueError('verified tool-server bundle is required')
        bundle=guard(data['bundle'])
        executable=guard(data['executable']) if data.get('executable') else None
        if not core and executable is None:raise ValueError('pinned legacy repair requires its compatible standalone launcher EXE')
        preview=component_repair.plan(root,bundle,executable,probe=probe)
        entries=preview['inventory']+preview.get('base_inventory',[])
        details={'preview':preview,'file_hashes':role_maintenance.content_inventory(root,entries)}
    else:raise ValueError('unsupported legacy operation')
    token=_token(operation,root,details,data)
    if action.endswith('-plan'):
        visible={key:value for key,value in (preview if operation!='rollback' else {'journal_status':details['journal']['status']}).items() if key not in ('inventory','base_inventory','file_hashes','old_state')}
        return dict(visible,installation_format='legacy',operation=operation,plan_id=token,requires_inference=operation=='repair' and not core,requires_owned_model_download=operation=='repair' and any(name.startswith('models/') or name=='ollama-runtime' for name in preview.get('repair_components',[])),keep_models=True,keep_config=True,ready=False)
    if data.get('confirm')!=token:raise ValueError('files, journal, arguments or policy changed after review')
    if cancelled():raise KeyboardInterrupt()
    class Quiet(io.TextIOBase):
        def write(self,value):return len(value)
        def flush(self):pass
    with contextlib.redirect_stdout(Quiet()),contextlib.redirect_stderr(Quiet()):
        if operation=='uninstall':result=uninstall.execute(root,preview['plan_sha256'],keep_models=True,keep_config=True,probe=probe,cancelled=cancelled)
        elif operation=='rollback':result=maintenance.recover(root,probe=probe,expected=fingerprint(details),cancelled=cancelled)
        else:
            if not core and data.get('allow_inference') is not True:raise ValueError('explicit live inference consent required')
            if any(name.startswith('models/') or name=='ollama-runtime' for name in preview.get('repair_components',[])) and data.get('allow_model_downloads') is not True:raise ValueError('explicit consent for owned model repair downloads required')
            from installer.core.download import download_artifact
            def download(artifact,cache,**kwargs):
                if cancelled():raise KeyboardInterrupt()
                def progress(*args):
                    if cancelled():raise KeyboardInterrupt()
                return download_artifact(artifact,cache,progress=progress)
            def repair(*args):return component_repair.repair(*args,download=download)
            result=maintenance.execute(root,bundle,executable,preview['plan_sha256'],planner=component_repair.plan,repair=repair,probe=probe,verify=role_maintenance.verify if core else maintenance.verify_staging,cancelled=cancelled)
    return dict(result,installation_format='legacy',ready=False)
