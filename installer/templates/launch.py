"""Installed Odessy CLI launcher. No dependency on the installer package."""
from __future__ import annotations
import argparse
import base64
import binascii
import json
import os
import runpy
import sys
from pathlib import Path


def main(argv=None):
    parser=argparse.ArgumentParser(description='Run the installed Odessy CLI in an explicit workspace.',allow_abbrev=False)
    parser.add_argument('--workspace',type=Path,help='Existing folder Odessy should operate in.')
    raw=list(sys.argv[1:] if argv is None else argv)
    if raw[:1]==['--encoded-args']:
        try:
            if len(raw)!=2:
                raise ValueError('Expected one encoded argument array.')
            raw=json.loads(base64.b64decode(raw[1],validate=True).decode('utf-8'))
            if not isinstance(raw,list) or any(not isinstance(value,str) for value in raw):
                raise ValueError('Expected a JSON string array.')
        except (ValueError,binascii.Error,UnicodeError):
            parser.error('Invalid encoded launcher argument array.')
    # PowerShell may consume -- before invoking a script. Only parse the
    # launcher's leading workspace option; preserve every CLI argument after it.
    end=0
    while end<len(raw):
        if raw[end]=='--workspace':
            end+=2
        elif raw[end].startswith('--workspace='):
            end+=1
        else:
            break
    args=parser.parse_args(raw[:end])
    forwarded=raw[end:]
    if forwarded[:1]==['--']:
        forwarded=forwarded[1:]
    root=Path(__file__).resolve().parent
    settings=json.loads((root/'odessy-application.json').read_text(encoding='utf-8'))
    if settings.get('schema_version')!=1 or settings.get('status')!='ready':
        parser.error('Application assembly is incomplete; rerun preparation into a new directory.')
    expected=Path(settings['python'])
    if Path(sys.executable).resolve()!=expected.resolve():
        parser.error('Use the private interpreter recorded in odessy-application.json or start-odessy.ps1.')
    help_only=not forwarded or forwarded in (['--help'],['-h'])
    if not help_only and args.workspace is None:
        parser.error('Specify --workspace before -- so the operation directory is explicit.')
    if args.workspace is not None:
        workspace=args.workspace.resolve()
        if not workspace.is_dir():
            parser.error('Workspace must be an existing directory.')
        if workspace==root or root in workspace.parents:
            parser.error('Choose a work folder outside the installed application.')
        os.chdir(workspace)
    runtime=Path(settings['runtime_dir'])
    if not (runtime/'node-runtime'/'node.exe').is_file() or not (root/'src'/'odessy'/'__main__.py').is_file():
        parser.error('Application or base runtime files are missing.')
    for key in list(os.environ):
        if key.upper() in {'PYTHONHOME','PYTHONPATH','NODE_OPTIONS','NODE_PATH'}:
            os.environ.pop(key)
    os.environ['PATH']=os.pathsep.join((str(runtime/'node-runtime'),str(runtime/'python-runtime'),os.environ.get('PATH','')))
    # Preserve ODESSY_* settings/API keys and the existing runtime's config precedence.
    sys.path.insert(0,str(root/'src'))
    sys.argv=['odessy',*(forwarded or ['--help'])]
    runpy.run_module('odessy',run_name='__main__')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
