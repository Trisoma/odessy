"""Typed local maintenance/pairing operations; no shell or general process RPC."""
from __future__ import annotations
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import concurrent.futures
from .contracts import fields
from .maintenance import guard,digest

MANAGE_ACTIONS={'plan','repair-plan','install','inspect','verify','repair','rollback','uninstall','service-plan','service-install','start','stop','status','service-remove'}
PACKAGE_ACTIONS={'package-review','package-install','package-launch','package-status','package-verify',
    'package-repair-review','package-repair','package-rollback-review','package-rollback',
    'package-configure-review','package-configure','package-uninstall-review','package-uninstall',
    'package-register','installed-spec','package-launch-ack','package-launch-status','package-launch-pending'}


def _versioned_root(parameters,name):
    from .lifecycle import format_of
    data=fields(parameters,{'action','root','payload','selection','confirm','version','workspace','mode'},name)
    value=data.get('root')
    if not isinstance(value,str) or not value.strip():raise ValueError('an existing installation root is required')
    root=guard(value)
    kind=format_of(root)
    if kind is None:raise ValueError('no Odessy installation exists at this location')
    if kind!='versioned':raise ValueError('this installation uses the legacy installer format; use legacy maintenance')
    return data,root


def _packaged(parameters,*,cancelled=lambda:False):
    """Installed lifecycle operations; internal paths never come from the user."""
    action=parameters.get('action')
    if action=='package-launch-pending':
        from .launch_receipts import pending
        data=fields(parameters,{'action','root','version'},'pending launch handoffs')
        return pending(data['root'],data.get('version'))
    if action in ('package-launch-ack','package-launch-status'):
        from . import launch_receipts
        data=fields(parameters,{'action','root','launch_id','state'},'launch handoff')
        root=guard(data['root'])
        if action=='package-launch-ack':
            return launch_receipts.acknowledge(root,data.get('launch_id'),data.get('state'))
        return launch_receipts.status(root,data.get('launch_id'))
    if action=='installed-spec':
        data=fields(parameters,{'action','root'},'installed launch')
        value=data.get('root')
        if not isinstance(value,str) or not value.strip():raise ValueError('an installation root is required')
        from .installed import spec
        return spec(value)
    if action=='package-status':
        from .maintenance import ManagedInstallation
        cursor=parameters.get('log_cursor',0)
        if type(cursor) is not int or cursor<0:raise ValueError('invalid log cursor')
        data,root=_versioned_root({key:value for key,value in parameters.items() if key!='log_cursor'},'packaged status')
        return ManagedInstallation(root).status(log_cursor=cursor)
    if action=='package-launch':
        from .windows_installation import launch
        launch_id=parameters.get('launch_id')
        data,root=_versioned_root({key:value for key,value in parameters.items() if key!='launch_id'},'packaged launch')
        mode=data.get('mode') or 'app'
        if mode not in ('app','manage','uninstall'):raise ValueError('invalid launch mode')
        process=launch(root,mode,launch_id=launch_id) if launch_id is not None else launch(root,mode)
        return {'launched':True,'pid':process.pid,'mode':mode,**({'launch_id':launch_id} if launch_id is not None else {})}
    if action=='package-verify':
        from .maintenance import ManagedInstallation
        data,root=_versioned_root(parameters,'packaged verify')
        manager=ManagedInstallation(root)
        record=manager.record()
        return dict(manager.verify(),registration=record.get('windows_registration',{}).get('state','none'))
    if action in ('package-configure-review','package-configure'):
        from .contracts import SetupSelection
        from .maintenance import ManagedInstallation
        data,root=_versioned_root(parameters,'packaged configuration')
        selection=SetupSelection.from_dict(data.get('selection'))
        manager=ManagedInstallation(root)
        plan=manager.configuration_plan(selection,data.get('workspace') or None)
        if action.endswith('-review'):return plan
        return manager.configure(selection,data.get('workspace') or None,data.get('confirm'))
    if action in ('package-uninstall-review','package-uninstall'):
        from .lifecycle import dispatch
        data,root=_versioned_root(parameters,'packaged uninstall')
        plan=dispatch({'action':'uninstall-plan','root':str(root)})
        if action.endswith('-review'):return plan
        if data.get('confirm')!=plan['plan_id']:raise ValueError('review the exact uninstall operation first')
        return dispatch({'action':'uninstall','root':str(root),'confirm':plan['plan_id']},cancelled=cancelled)
    if action=='package-register':
        from .maintenance import ManagedInstallation, atomic
        from .windows_installation import register
        data,root=_versioned_root(parameters,'packaged registration')
        manager=ManagedInstallation(root)
        record=manager.record()
        payload=data.get('payload') or (record.get('windows_registration') or {}).get('helper')
        register(root,record,payload=payload or None)
        atomic(root/'installation.json',record)
        return {'registered':True,'state':record.get('windows_registration',{}).get('state','none')}
    if action in ('package-rollback-review','package-rollback'):
        from .lifecycle import dispatch
        data,root=_versioned_root(parameters,'packaged rollback')
        return dispatch({'action':'rollback-plan' if action.endswith('-review') else 'rollback',
            'root':str(root),'version':data.get('version'),
            **({'confirm':data.get('confirm')} if action=='package-rollback' else {})},cancelled=cancelled)
    from .package_context import context
    from .contracts import SetupSelection
    from .maintenance import ManagedInstallation, atomic
    from .windows_installation import register
    data=fields(parameters,{'action','root','payload','selection','confirm'},'packaged operation')
    package=context(payload=data.get('payload'),root=data.get('root'))
    root=guard(package['root'])
    from .lifecycle import format_of
    if format_of(root)=='legacy':raise ValueError('use legacy maintenance for this installation')
    manager=ManagedInstallation(root,runtime_source=Path(package['payload_root'])/'runtime',payload=package['payload_root'])
    if action=='package-review':return manager.plan(package['bundle'],SetupSelection.from_dict(data.get('selection')))
    if action in ('package-repair-review','package-repair'):
        if action.endswith('-review'):
            selection=SetupSelection.from_dict(manager.record()['selection'])
            return manager.plan(package['bundle'],selection)
        result=manager.repair(package['bundle'],confirm=data.get('confirm'),cancelled=cancelled)
    else:
        result=manager.install(package['bundle'],SetupSelection.from_dict(data.get('selection')),data.get('confirm'),cancelled=cancelled)
    record=manager.record()
    registration='skipped'
    try:
        registered=register(root,record,payload=package['payload_root'])
        registration=registered.get('state','complete') if registered else 'unsupported'
    except (RuntimeError,OSError,ValueError) as error:
        # The installation is active; registration stays a recoverable, recorded step.
        registration='incomplete'
        result['registration_error']=str(error)
    atomic(root/'installation.json',record)
    return dict(result,installation_format='versioned',registration=registration)


def manage(parameters,*,cancelled=lambda:False):
    if os.environ.get("ODESSY_PORTABLE_ROOT"):
        from .portable import dispatch
        return dispatch(parameters)
    if parameters.get('action') in PACKAGE_ACTIONS:
        return _packaged(parameters,cancelled=cancelled)
    if parameters.get('action')=='context':
        fields(parameters,{'action','root','payload','selection'},'context')
        from .package_context import context
        return context(payload=parameters.get('payload'),root=parameters.get('root') or None)
    data=fields(parameters,{'action','root','bundle','config','role','confirm','version','workspace','port','node_link','node_server','listen','executable','allow_inference','allow_model_downloads'},'management')
    from .lifecycle import dispatch
    routed=dispatch(data,cancelled=cancelled)
    if routed is not None:return routed
    if data.get('action') not in MANAGE_ACTIONS: raise ValueError('unsupported management operation')
    data=dict(data)
    if data['action'] in ('plan','install') and not data.get('config') and not data.get('role'):
        data['role']='combined'
    argv=[data['action']]
    for key,value in data.items():
        if key in ('action','allow_inference','allow_model_downloads'):continue
        if key=='executable':
            if value:raise ValueError('launcher EXE only applies to legacy maintenance')
            continue
        if not isinstance(value,(str,int)) or isinstance(value,bool) or len(str(value))>4096 or any(c in str(value) for c in '\n\r\0'): raise ValueError('invalid management argument')
        if value!='': argv.extend(['--'+key.replace('_','-'),str(value)])
    from .manage import main
    output,error=io.StringIO(),io.StringIO()
    with contextlib.redirect_stdout(output),contextlib.redirect_stderr(error): status=main(argv,cancel_check=cancelled)
    if status: raise ValueError('management operation failed; inspect before retrying')
    result=json.loads(output.getvalue())
    from .lifecycle import format_of
    result['installation_format']=format_of(Path(data['root'])) or 'new'
    return result


def bounded_native(argv,timeout=15):
    process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
        **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
    pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
    def read():
        chunks=[];size=0
        while True:
            chunk=process.stdout.read(65536)
            if not chunk:break
            size+=len(chunk)
            if size>1024*1024:raise ValueError('native response exceeds limit')
            chunks.append(chunk)
        return b''.join(chunks)
    future=pool.submit(read)
    try:
        data=future.result(timeout=timeout)
        if process.wait(timeout=2):raise ValueError('native operation failed')
        return data.decode('utf-8')
    finally:
        if process.poll() is None:process.kill()
        process.wait();process.stdout.close();pool.shutdown(wait=True)


def pair(parameters,*,runner=None):
    data=fields(parameters,{'action','link','alias','certificate','fingerprint','address','workspace','output','review','confirm'},'pairing')
    action=data.get('action');link=guard(data.get('link',''))
    if not data.get('link') or not link.is_file(): raise ValueError('select an existing odessy-link executable')
    argv=[str(link)]
    alias=data.get('alias','')
    if action in ('enroll','grant','revoke') and not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',alias): raise ValueError('invalid peer alias')
    certificate_hash=None
    if action=='list': argv+=['peers','list','--json']
    elif action=='discover': argv+=['discover','--seconds','2']
    elif action=='init':argv+=['init']
    elif action=='export':
        output=guard(data.get('output',''))
        if not data.get('output') or output.exists():raise ValueError('choose a new identity export path')
        argv+=['identity','export','--out',str(output)]
    elif action=='enroll':
        certificate=guard(data.get('certificate',''))
        if not certificate.is_file() or certificate.stat().st_size>65536:raise ValueError('invalid certificate bundle')
        fingerprint=data.get('fingerprint','').replace(':','').lower()
        if not re.fullmatch('[a-f0-9]{64}',fingerprint):raise ValueError('explicit out-of-band fingerprint required')
        certificate_hash=digest(certificate)
        argv+=['pair','add','--alias',alias,'--certificate',str(certificate),'--expect-sha256',fingerprint]
        address=data.get('address','')
        if address:
            if len(address)>512 or any(c.isspace() for c in address):raise ValueError('invalid peer address')
            argv+=['--address',address]
    elif action=='grant':
        workspace=guard(data.get('workspace',''))
        if not data.get('workspace') or not workspace.is_dir():raise ValueError('explicit local workspace required')
        argv+=['peers','grant',alias,'--capability','mcp','--workspace',str(workspace)]
    elif action=='revoke':argv+=['peers','revoke',alias]
    else:raise ValueError('unsupported pairing operation')
    registry_snapshot=None
    if action in ('grant','revoke','enroll','export'):
        if runner is None:
            registry_snapshot=json.loads(bounded_native([str(link),'peers','list','--json'],timeout=10))
        else:registry_snapshot=json.loads(runner([str(link),'peers','list','--json']))
    payload={'registry_snapshot':registry_snapshot,'argv':argv,'binary_sha256':digest(link),'certificate_sha256':certificate_hash,'state_directory':os.environ.get('ODESSY_LINK_STATE_DIR','current-user-default')}
    plan_id=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    if data.get('review') is True:
        return {'plan_id':plan_id,'operation':action,'arguments':argv[1:],'grants_changed':action in ('grant','revoke'),'node_local_only':True,'verification':'Compare fingerprints through an independent trusted channel before enrollment.'}
    if action not in ('list','discover') and data.get('confirm')!=plan_id:raise ValueError('review exact pairing operation before confirming')
    if runner is None:
        text=bounded_native(argv)
    else:text=runner(argv)
    return {'operation':action,'peers':json.loads(text) if action=='list' else None,'message':text if action!='list' else '', 'grants_changed':action in ('grant','revoke'),'node_local_only':True}


