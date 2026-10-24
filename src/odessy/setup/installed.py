"""Installed startup: validated runtime, recorded configuration, owned coordinator.

The installed desktop must not guess an interpreter, reuse the extracted installer
payload, or fall back to a demo session. Everything checked here is read-only; the
launcher owns the child process and supplies the authenticated loopback token.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

from .contracts import SetupSelection, public
from .lifecycle import format_of
from .maintenance import ManagedInstallation, default_workspace, digest, guard, read_json, validated_workspace

EXECUTABLES=('Scripts/python.exe','bin/python3','desktop/odessy_desktop.exe')
MAX_CONFIGURATION_BYTES=65536


def active_configuration(root):
    """The recorded active configuration, or None for installations predating it."""
    root=guard(root)
    path=guard(root/'configuration.json')
    if not path.is_file():return None
    if path.stat().st_size>MAX_CONFIGURATION_BYTES:raise ValueError('active configuration exceeds its size limit')
    data=read_json(path)
    if (not isinstance(data,dict) or set(data)!={'schema_version','selection','workspace'}
            or data['schema_version']!=1):
        raise ValueError('invalid active configuration record')
    return data


def spec(root):
    """Validate the installed version, executables and configuration before launch."""
    root=guard(root)
    kind=format_of(root)
    if kind!='versioned':
        raise ValueError('no manageable Odessy installation exists at this location')
    record=ManagedInstallation(root).record()
    version=record.get('active')
    if not version:
        raise ValueError('the installation has no active version; repair it in management')
    files=record['versions'][version]
    base=guard(root/'versions'/version)
    python_name='Scripts/python.exe' if os.name=='nt' else 'bin/python3'
    changed=[]
    for name in (*EXECUTABLES,python_name):
        if name not in files:continue
        path=guard(base/name)
        if not path.is_file() or digest(path)!=files[name]:changed.append(name)
    if python_name not in files:raise ValueError('the installed runtime is incomplete; repair it in management')
    if 'desktop/odessy_desktop.exe' not in files:
        raise ValueError('this installation has no desktop application; open Odessy management instead')
    if changed:
        raise ValueError('installed files were modified or damaged; repair before launching: '+', '.join(sorted(set(changed))))
    configuration=active_configuration(root)
    if configuration is None:
        stored=read_json(guard(base/'selection.json'))
        if not isinstance(stored,dict) or 'selection' not in stored:raise ValueError('invalid installed selection record')
        selection=SetupSelection.from_dict(stored['selection'])
        workspace=default_workspace()
        source='version'
    else:
        selection=SetupSelection.from_dict(configuration['selection'])
        workspace=validated_workspace(configuration['workspace'],root)
        source='configuration'
    helper=record.get('windows_registration',{}).get('helper')
    payload=None
    if isinstance(helper,str) and helper:
        candidate=guard(Path(helper))
        if candidate.is_dir() and (candidate/'setup-context.json').is_file():payload=str(candidate)
    return {'schema_version':1,'root':str(root),'installation_id':record['id'],'version':version,
            'python':str(guard(base/python_name)),'desktop':str(guard(base/'desktop/odessy_desktop.exe')),
            'selection':public(selection),'configuration_source':source,'role':selection.role,
            'model_configured':selection.model is not None,'voice_enabled':selection.voice.enabled,
            'workspace':str(workspace),'workspace_exists':workspace.is_dir(),
            'config':str(root/'configuration.json'),'payload':payload,
            'registration':record.get('windows_registration',{}).get('state','none'),
            'service_recorded':(root/'service.json').exists(),
            'ready':selection.model is not None and selection.role!='execution'}


def serve(argv=None):
    parser=argparse.ArgumentParser(prog='python -m odessy.setup.installed',
        description='Start the installed coordinator on an authenticated loopback port.')
    parser.add_argument('--root',required=True)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--exit-on-stdin-close',action='store_true',
        help='Stop when the owning launcher closes its stdin pipe.')
    args=parser.parse_args(argv)
    info=spec(args.root)
    if info['role']=='execution':
        raise ValueError('this installation is an execution node; start its service instead of a coordinator')
    if not info['model_configured']:
        raise ValueError('no model is configured; open Odessy management and configure one first')
    token=os.environ.get('ODESSY_API_TOKEN','').strip()
    if not token:
        raise ValueError('the launcher must supply an authenticated loopback token')
    if not (1<=args.port<=65535):
        raise ValueError('invalid loopback port')
    workspace=Path(info['workspace'])
    workspace.mkdir(parents=True,exist_ok=True)
    selection=SetupSelection.from_dict(info['selection'])
    from .service_entry import run
    return run(selection,workspace=workspace,host=args.host,port=args.port,token=token,
               watch_stdin=args.exit_on_stdin_close)


def main(argv=None):
    try:
        serve(argv)
        return 0
    except (ValueError,RuntimeError,OSError) as error:
        print(json.dumps({'ready':False,'error':str(error)}),file=sys.stdout,flush=True)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
