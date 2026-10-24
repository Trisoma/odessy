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


def manage(parameters,*,cancelled=lambda:False):
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
    process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
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
