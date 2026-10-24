"""Verified, packaged setup defaults. Never discover payloads from the cwd."""
from __future__ import annotations
import os,re,sys
from pathlib import Path
from .maintenance import guard,read_json,digest

def context(*,payload=None,local_app_data=None,root=None):
    payload=guard(payload or os.environ.get('ODESSY_SETUP_PAYLOAD') or Path(sys.executable).parent)
    manifest=read_json(payload/'setup-context.json')
    if set(manifest)!={'schema_version','components','files'} or manifest['schema_version']!=1:
        raise ValueError('unsupported setup context')
    files=manifest['files'];components=manifest['components']
    if not isinstance(files,dict) or not files or len(files)>100000 or not isinstance(components,list) or any(not isinstance(x,str) for x in components):
        raise ValueError('invalid setup inventory')
    for name,sha in files.items():
        if not isinstance(name,str) or not name or '\\' in name or ':' in name or name.startswith('/') or '..' in Path(name).parts or not re.fullmatch('[a-f0-9]{64}',str(sha)):
            raise ValueError('invalid setup inventory path')
        file=guard(payload/name)
        if not file.is_file() or digest(file)!=sha:raise ValueError('setup payload checksum mismatch')
    base=Path(local_app_data or os.environ.get('LOCALAPPDATA') or Path.home()/'.local/share')
    target=guard(root or base/'Odessy')
    from .lifecycle import format_of
    format_=format_of(target)
    configuration=target/'configuration.json'
    selection=None
    if format_=='versioned':
        from .maintenance import ManagedInstallation
        record=ManagedInstallation(target).record()
        selection=record.get('selection')
        if record.get('active'):configuration=target/'versions'/record['active']/'selection.json'
    return {'schema_version':1,'launch_mode':'manage' if format_ else 'setup','root':str(target),'bundle':str(payload/'wheelhouse'),'config':str(configuration),'selection':selection,'installation_format':format_ or 'new','payload_root':str(payload),'components':components,'verified':True}
