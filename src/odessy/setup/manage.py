"""Explicit headless native lifecycle; plans first, exact consent for mutation."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import threading
from .cli import _load
from .contracts import SetupSelection
from .maintenance import ManagedInstallation, Cancelled, guard
from .services import ServiceManager, ServiceSpec


def main(argv=None,*,cancel_check=lambda:False):
    parser=argparse.ArgumentParser(prog='odessy setup manage')
    parser.add_argument('operation',choices=('plan','repair-plan','rollback-plan','uninstall-plan','install','inspect','verify','repair','rollback','uninstall','service-plan','service-install','start','stop','status','service-remove'))
    parser.add_argument('--root',required=True)
    parser.add_argument('--bundle')
    parser.add_argument('--python-runtime')
    parser.add_argument('--config')
    parser.add_argument('--role',choices=('combined','coordinator','execution'))
    parser.add_argument('--confirm')
    parser.add_argument('--version')
    parser.add_argument('--workspace')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--executable')
    parser.add_argument('--allow-inference',action='store_true')
    parser.add_argument('--allow-model-downloads',action='store_true')
    parser.add_argument('--node-link')
    parser.add_argument('--node-server')
    parser.add_argument('--listen',default='127.0.0.1:7443')
    args=parser.parse_args(argv)
    cancelled=threading.Event()
    old={}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT,signal.SIGTERM):
            old[sig]=signal.signal(sig,lambda *_:cancelled.set())
    try:
        from .lifecycle import dispatch,format_of
        lifecycle_args={'action':args.operation,'root':args.root}
        for name in ('bundle','config','role','confirm','version','workspace','port','node_link','node_server','listen','executable'):
            value=getattr(args,name)
            if value is not None:lifecycle_args[name]=value
        if args.allow_inference:lifecycle_args['allow_inference']=True
        if args.allow_model_downloads:lifecycle_args['allow_model_downloads']=True
        routed=dispatch(lifecycle_args,cancelled=lambda:cancelled.is_set() or cancel_check())
        if routed is not None:
            print(json.dumps(routed,indent=2));return 0
        manager=ManagedInstallation(args.root,runtime_source=args.python_runtime)
        if args.config and args.role: raise ValueError('select config or role, not both')
        selection=_load(args.config) if args.config else SetupSelection(role=args.role) if args.role else None
        if args.operation in ('plan','repair-plan','install','repair'):
            if not args.bundle: raise ValueError('an offline bundle is required')
            if args.operation=='repair-plan':
                result=manager.plan(args.bundle,SetupSelection.from_dict(manager.record()['selection']))
            elif args.operation=='repair':
                result=manager.repair(args.bundle,confirm=args.confirm,cancelled=lambda:cancelled.is_set() or cancel_check())
            else:
                if selection is None: raise ValueError('explicit selection or role required')
                result=manager.plan(args.bundle,selection) if args.operation=='plan' else manager.install(args.bundle,selection,args.confirm,cancelled=lambda:cancelled.is_set() or cancel_check())
        elif args.operation=='inspect':
            record=manager.record()
            result={'installation_id':record['id'],'active':record['active'],'versions':list(record['versions']),'selection':record.get('selection'),'service_recorded':(manager.root/'service.json').exists(),'ready':False}
        elif args.operation=='verify': result=manager.verify()
        elif args.operation=='rollback': result=manager.rollback(args.version,confirm=args.confirm)
        elif args.operation=='uninstall': result=manager.uninstall(confirm=args.confirm)
        else:
            services=ServiceManager(manager.root)
            if args.operation in ('service-plan','service-install'):
                record=manager.record()
                if not record['active'] or not manager.verify()['valid']: raise ValueError('verify a complete active installation first')
                selection=SetupSelection.from_dict(record['selection'])
                workspace=guard(args.workspace or '')
                if not args.workspace or not workspace.is_dir(): raise ValueError('explicit existing workspace required')
                if not 1<=args.port<=65535: raise ValueError('invalid API port')
                base=manager.root/'versions'/record['active']
                if selection.role=='execution':
                    if not args.node_link or not args.node_server: raise ValueError('execution service requires existing odessy-link and Node server paths')
                    link=guard(args.node_link);node=guard(args.node_server)
                    if not link.is_file() or not node.is_file(): raise ValueError('missing execution components')
                    command=(str(link),'serve','--listen',args.listen,'--workspace',str(workspace),'--node-server',str(node))
                else:
                    if selection.model is None: raise ValueError('configure a model before creating coordinator service')
                    python=base/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
                    command=(str(python),'-m','odessy.setup.service_entry','--selection',str(base/'selection.json'),'--workspace',str(workspace),'--port',str(args.port))
                spec=ServiceSpec(record['id'],command,str(workspace))
                _,content=services._expected(spec)[:2]
                import hashlib
                consent=hashlib.sha256((record['id']+content).encode()).hexdigest()
                if args.operation=='service-plan': result={'service_plan_id':consent,'definition':content,'scope':'current-user','boot_before_login':False,'grants_changed':False}
                else:
                    if args.confirm!=consent: raise ValueError('review exact service plan before registration')
                    with manager.lock(): result=services.install(spec)
            else:
                if args.operation!='status' and args.confirm!=manager.identity(): raise ValueError('confirm exact installation identity')
                with manager.lock(): result=services.action(services.load(), 'remove' if args.operation=='service-remove' else args.operation)
        result.setdefault('installation_format',format_of(Path(args.root)) or 'new')
        print(json.dumps(result,indent=2));return 0
    except KeyboardInterrupt:
        print(json.dumps({'error':'Operation cancelled. Inspect checkpoints and journals before resuming.'}),file=sys.stderr);return 130
    except (ValueError,RuntimeError,OSError,KeyError,TypeError):
        print(json.dumps({'error':'Setup operation failed or was cancelled. Inspect ownership, inputs and native permissions; no automatic retry was performed.'}),file=sys.stderr)
        return 2
    finally:
        for sig,handler in old.items(): signal.signal(sig,handler)

if __name__=='__main__': raise SystemExit(main())
