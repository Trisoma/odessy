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


PAIRING_STATES = {'closed', 'discoverable', 'authenticating', 'awaiting_approval',
    'enrolling', 'paired_no_access', 'authorizing_access', 'connecting', 'ready',
    'rejected', 'expired', 'cancelled', 'failed', 'incomplete'}
_PAIRING_ALIAS = re.compile(r'[A-Za-z0-9_-]{1,64}')
_PAIRING_ID = re.compile(r'[A-Za-z0-9_-]{1,128}')
_PAIRING_TOKEN = re.compile(r'[A-Za-z0-9_-]{1,256}')


def _pairing_snapshot(text, command):
    """Validate the native pairing document; never render a code the caller sent."""
    try:
        reply = json.loads(text)
    except (TypeError, ValueError):
        raise ValueError('Pairing component returned an unreadable response; update odessy-link.') from None
    if (not isinstance(reply, dict) or reply.get('schema_version') != 1
            or type(reply.get('supported')) is not bool or reply.get('state') not in PAIRING_STATES
            or (reply.get('supported') is False and reply.get('state') != 'closed')):
        raise ValueError('Pairing component returned an unsupported state; update odessy-link.')
    for key, limit in (('session_id', 128), ('review_token', 256), ('reason', 200)):
        value = reply.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise ValueError('Pairing component returned an invalid field; update odessy-link.')
        reply[key] = value or ''
    if reply['supported'] and reply['state'] != 'closed' and not reply['session_id']:
        raise ValueError('Pairing component reported a state without a session; update odessy-link.')
    if command != 'begin':
        # Only the host's own status window shows a code, and only when it opened one.
        reply.pop('code', None)
    reply['component_available'] = True
    return reply


def _unavailable_pairing(detail=''):
    return {'schema_version': 1, 'supported': False, 'state': 'closed',
        'reason': 'component_unavailable', 'session_id': '', 'review_token': '',
        'component_available': False,
        'message': 'The pairing component could not be run. Install or repair odessy-link; '
                   'existing paired devices and execution-target review still work.' + (
                       ' ' + detail.strip()[:200] if detail.strip() else '')}


def _native_json(argv,timeout,stdin_text=None):
    """Run a fixed native command, bounded in time and output, without a shell."""
    import queue
    import threading
    import time
    process=subprocess.Popen(argv,stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
    replies=[queue.Queue(maxsize=1),queue.Queue(maxsize=1)]
    oversized=threading.Event()
    def drain(stream,limit,target):
        kept=bytearray()
        try:
            while True:
                chunk=stream.read(8192)
                if not chunk:break
                room=limit-len(kept)
                if len(chunk)>room:oversized.set()
                kept.extend(chunk[:max(0,room)])
        finally:
            target.put(bytes(kept))
            stream.close()
    for stream,limit,target in zip((process.stdout,process.stderr),(262144,65536),replies):
        threading.Thread(target=drain,args=(stream,limit,target),daemon=True).start()
    if stdin_text is not None:
        try:process.stdin.write(stdin_text.encode('utf-8'));process.stdin.close()
        except BrokenPipeError:pass
    deadline=time.monotonic()+timeout
    failure=None
    while process.poll() is None:
        if oversized.is_set():failure='The pairing component returned too much data.';break
        if time.monotonic()>=deadline:
            failure='The pairing component did not respond in time. Inspect both devices before retrying.';break
        time.sleep(.01)
    if failure:
        process.kill()
        try:process.wait(timeout=.5)
        except subprocess.TimeoutExpired:pass
        # Never wait for inherited descendant pipes on timeout. Drain threads retain
        # fixed caps only, and exit when those handles close.
        raise ValueError(failure)
    try:
        stdout=replies[0].get(timeout=.5)
        stderr=replies[1].get(timeout=.5)
    except queue.Empty:
        raise ValueError('Pairing output is incomplete. Inspect the operation before retrying.') from None
    if oversized.is_set():raise ValueError('The pairing component returned too much data.')
    return process.returncode,stdout.decode('utf-8','replace'),stderr.decode('utf-8','replace')



def _short_code(data,*,runner=None):
    """Drive the native enrollment sessions; no code is derived, compared or stored here."""
    command=data.get('command')
    if command not in ('status','begin','join','confirm','cancel'):
        raise ValueError('Unsupported pairing-session command.')
    profile=data.get('profile')
    if profile not in (None,'','test-adapter'):
        raise ValueError('Unsupported pairing profile.')
    try:
        from odessy.tools.paired import link_binary
        binary=guard(data['link']) if data.get('link') else link_binary().resolve()
    except (ValueError,OSError) as error:
        return _unavailable_pairing(str(error))
    if not binary.is_file():
        return _unavailable_pairing('the connection component is missing')
    argv=[str(binary),'pairing',command]
    stdin_text=None
    if profile=='test-adapter':argv+=['--profile','test-adapter']
    if command=='begin':
        workspace=(data.get('workspace') or '').strip()
        if workspace:
            path=guard(workspace)
            if not path.is_dir():raise ValueError('Choose an existing folder to share with the other device.')
            argv+=['--workspace',str(path)]
    elif command=='join':
        address=(data.get('address') or '').strip()
        if not address or len(address)>512 or any(character.isspace() for character in address):
            raise ValueError('Choose a nearby device before entering its code.')
        code=str(data.get('code') or '')
        if not re.fullmatch(r'[0-9]{6}',code):
            raise ValueError('Enter the six digits exactly as the other computer shows them.')
        # The code travels on stdin: a command line is visible to other local users on
        # some platforms, and the native side reads exactly one bounded JSON line.
        argv+=['--address',address,'--code-stdin']
        stdin_text=json.dumps({'code':code})
    elif command=='confirm':
        session=data.get('session_id') or ''
        token=data.get('review_token') or ''
        if not _PAIRING_ID.fullmatch(session) or not _PAIRING_TOKEN.fullmatch(token):
            raise ValueError('This pairing request is stale; review it again on this computer.')
        argv+=['--session-id',session,'--review-token',token]
        workspace=(data.get('workspace') or '').strip()
        if workspace:
            path=guard(workspace)
            if not path.is_dir():raise ValueError('Choose an existing folder to share with the other device.')
            argv+=['--workspace',str(path)]
    elif command=='cancel':
        session=data.get('session_id') or ''
        if session:
            if not _PAIRING_ID.fullmatch(session):raise ValueError('Unknown pairing session.')
            argv+=['--session-id',session]
    timeout=30 if command=='join' else 15
    try:
        if runner is not None:
            return _pairing_snapshot(runner(argv,stdin_text),command)
        status,stdout,_stderr=_native_json(argv,timeout,stdin_text)
    except (OSError,subprocess.SubprocessError) as error:
        return _unavailable_pairing(str(error))
    if status!=0:
        try:
            return _pairing_snapshot(stdout,command)
        except ValueError:
            if isinstance(stdout,str) and stdout.strip().endswith('}'):
                raise
        raise ValueError('The pairing component refused the request. Inspect the component status before retrying.')
    return _pairing_snapshot(stdout,command)


def _discovery_devices(devices):
    """One untrusted candidate per advertised identity, with pairing endpoints first."""
    import ipaddress
    grouped={}
    def rank(item):
        address=item.get('address') or ''
        host=address.rsplit(':',1)[0].strip('[]')
        try: version=ipaddress.ip_address(host).version
        except ValueError: version=9
        return (item.get('purpose')!='pairing',version,address)
    for item in devices:
        if not isinstance(item,dict) or not item.get('usable') or not isinstance(item.get('address'),str):continue
        fingerprint=item.get('advertised_fingerprint')
        key=fingerprint.lower() if isinstance(fingerprint,str) and re.fullmatch('[a-fA-F0-9]{64}',fingerprint) else item['address']
        candidate=dict(item,trusted=False)
        if key not in grouped or rank(candidate)<rank(grouped[key]):grouped[key]=candidate
    return sorted(grouped.values(),key=lambda item:(item.get('name',''),item['address']))


def pair(parameters,*,runner=None):
    data=fields(parameters,{'action','link','alias','certificate','fingerprint','address','workspace','output','review','confirm','capability','enabled','revision','command','code','session_id','review_token','profile'},'pairing')
    action=data.get('action')
    if action=='short-code':
        return _short_code(data,runner=runner)
    if action=='outbound-inspect':
        from odessy.execution.outbound import read
        return read()
    if action in ('outbound-review','outbound-save'):
        from odessy.execution import outbound
        from odessy.tools.paired import link_binary, PairedConfigError
        try: binary=guard(data['link']) if data.get('link') else link_binary().resolve()
        except PairedConfigError:
            if data.get('enabled') is not False:raise
            binary=None
        prober=None
        if runner is not None:
            def prober(host):
                listing=json.loads(runner([str(host.binary),'peers','list','--json']))
                peer=next((item for item in listing.get('peers',[]) if item.get('alias')==host.alias),None)
                return dict(ok=True,configured_alias_present=peer is not None,
                    grant_revision=listing.get('grant_revision'),
                    fingerprint_sha256=peer.get('fingerprint_sha256') if peer else None,
                    revoked=peer.get('revoked') if peer else None)
        if action=='outbound-review':
            result=outbound.review(data.get('alias'),data.get('enabled'),binary,prober=prober)
            return dict(result,operation='outbound-consent',arguments=[
                ('Enable' if result['enabled'] else 'Disable')+' outbound tools for '+result['alias'],
                'Verified certificate: '+result['fingerprint_sha256'],result['message']])
        return outbound.save(data.get('alias'),data.get('enabled'),binary,data.get('revision'),data.get('confirm'),prober=prober)
    if data.get('link'):link=guard(data['link'])
    else:
        from odessy.tools.paired import link_binary
        link=link_binary().resolve()
    if not link.is_file(): raise ValueError('select an existing odessy-link executable')
    argv=[str(link)]
    alias=data.get('alias','')
    if action in ('enroll','grant','revoke') and not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',alias): raise ValueError('invalid peer alias')
    certificate_hash=None
    if action=='list': argv+=['peers','list','--json']
    elif action=='discover': argv+=['discover','--seconds','2','--json']
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
        capability=data.get('capability','mcp')
        if capability not in ('mcp','telemetry.read'):raise ValueError('unsupported local capability')
        argv+=['peers','grant',alias,'--capability',capability]
        if capability=='mcp':
            workspace=guard(data.get('workspace',''))
            if not data.get('workspace') or not workspace.is_dir():raise ValueError('explicit local workspace required')
            argv+=['--workspace',str(workspace)]
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
    result={'operation':action,'link':str(link),'peers':json.loads(text) if action=='list' else None,'message':text if action not in ('list','discover') else '', 'grants_changed':action in ('grant','revoke'),'node_local_only':True}
    if action=='list':
        from odessy.execution.outbound import read
        try:result['outbound']=read()
        except ValueError:result['outbound']={'enabled':False,'error':'Execution consent requires inspection; no outbound authority is inferred.'}
    if action=='discover':
        discovery=json.loads(text)
        if not isinstance(discovery,dict) or discovery.get('schema_version')!=1 or not isinstance(discovery.get('devices'),list) or len(discovery['devices'])>256:
            raise ValueError('Unsupported discovery response; update odessy-link.')
        result['devices']=_discovery_devices(discovery['devices'])
        result['message']='Nearby advertisements are unverified. Selecting a device never enrolls it or grants access.'
    return result


