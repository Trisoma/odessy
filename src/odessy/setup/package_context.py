"""Verified, packaged setup defaults. Never discover payloads from the cwd."""
from __future__ import annotations
import os,re,sys
from pathlib import Path
from .maintenance import guard,read_json,digest

#: Payload top-level directories produced by tool/build_setup_bundle.py.
COMPONENT_DIRECTORIES=('runtime','desktop','node','tools','link','monitor')
KNOWN_DIRECTORIES=frozenset((*COMPONENT_DIRECTORIES,'bootstrap','wheelhouse'))
KNOWN_FILES=frozenset(('install.py','install.cmd','install.sh','runtime-manifest.json',
                       'LICENSE','THIRD-PARTY-NOTICES.md'))
MAX_FILES=100000
MAX_FILE_BYTES=4*1024**3


def required_components(role,*,runtime=False):
    """Components without which the selected role cannot start at all."""
    required=set()
    if runtime:required.add('python')
    if role!='execution':required.add('desktop')
    return required


def optional_components(role):
    """Components that extend a role; absence is reported, never invented."""
    optional=set()
    if role!='coordinator':optional.update(('node','tools'))
    if role!='execution':optional.add('monitor')
    if role!='combined':optional.add('link')
    return optional


def _prefix(name):
    return 'runtime' if name=='python' else name


def _inventory(payload,manifest):
    files=manifest['files']
    if not isinstance(files,dict) or not files or len(files)>MAX_FILES:
        raise ValueError('invalid setup inventory')
    seen=set()
    # Audit every declared path before trusting any of them.
    for name,sha in files.items():
        if not isinstance(name,str) or not name or '\\' in name or ':' in name or name.startswith('/') or '..' in Path(name).parts:
            raise ValueError('invalid setup inventory path')
        folded=name.casefold()
        if folded in seen:raise ValueError('duplicate setup inventory path')
        seen.add(folded)
        top=name.split('/')[0]
        if top not in KNOWN_DIRECTORIES and name not in KNOWN_FILES:
            raise ValueError('unsupported setup payload path')
        if not re.fullmatch('[a-f0-9]{64}',str(sha)):raise ValueError('invalid setup inventory path')
    # Then prove the payload matches that inventory, in bounded reads.
    for name,sha in files.items():
        file=guard(payload/name)
        if not file.is_file():raise ValueError('setup payload is incomplete')
        if file.stat().st_size>MAX_FILE_BYTES:raise ValueError('setup payload member exceeds its size limit')
        if digest(file)!=sha:raise ValueError('setup payload checksum mismatch')
    return files


def validate_components(manifest,role,*,runtime=False):
    """Prove the payload can satisfy the role, then choose what to copy.

    A declared component with no files is a broken bundle. A component that is
    merely absent (for example the optional Node tool server) is reported as
    unavailable instead of failing an otherwise usable installation.
    """
    required=required_components(role,runtime=runtime)
    listed=manifest.get('components')
    if not isinstance(listed,list) or any(not isinstance(item,str) for item in listed):
        raise ValueError('invalid setup component inventory')
    missing=[name for name in sorted(required) if name not in listed]
    if missing:raise ValueError('setup payload lacks required components: '+', '.join(missing))
    files=manifest.get('files')
    if not isinstance(files,dict):raise ValueError('invalid setup inventory')
    present={name.split('/')[0] for name in files}
    empty=[name for name in sorted(set(listed)) if _prefix(name) not in present]
    if empty:raise ValueError('setup payload declares components with no files: '+', '.join(empty))
    selected={'link'}
    if role!='execution':selected.update(('desktop','monitor'))
    if role!='coordinator':selected.update(('node','tools'))
    absent=[name for name in sorted(optional_components(role)) if name not in listed]
    return {'copy':selected,'absent':absent}


def context(*,payload=None,local_app_data=None,root=None):
    payload=guard(payload or os.environ.get('ODESSY_SETUP_PAYLOAD') or Path(sys.executable).parent)
    manifest=read_json(payload/'setup-context.json')
    if set(manifest)!={'schema_version','components','files'} or manifest['schema_version']!=1:
        raise ValueError('unsupported setup context')
    components=manifest['components']
    if not isinstance(components,list) or any(not isinstance(x,str) for x in components):
        raise ValueError('invalid setup inventory')
    files=_inventory(payload,manifest)
    base=Path(local_app_data or os.environ.get('LOCALAPPDATA') or Path.home()/'.local/share')
    # The extracted installer payload lives under LOCALAPPDATA/Odessy/Setup; the
    # installation must not share that directory, so empty-root ownership checks
    # stay strict instead of being relaxed for installer bookkeeping.
    target=guard(root or base/'Odessy'/'App')
    from .lifecycle import format_of
    format_=format_of(target)
    selection=None
    workspace=None
    installation_id=None
    active=None
    registration='none'
    configuration_recorded=False
    if format_=='versioned':
        from .maintenance import ManagedInstallation
        record=ManagedInstallation(target).record()
        installation_id=record.get('id')
        active=record.get('active')
        registration=record.get('windows_registration',{}).get('state','none')
        selection=record.get('selection')
        if (target/'configuration.json').is_file():
            configuration_recorded=True
            active_configuration=read_json(target/'configuration.json')
            selection=active_configuration.get('selection')
            workspace=active_configuration.get('workspace')
    return {'schema_version':1,'launch_mode':'manage' if format_ else 'setup','root':str(target),
            'bundle':str(payload/'wheelhouse'),'config':str(target/'configuration.json'),
            'selection':selection,'installation_format':format_ or 'new','installation_id':installation_id,
            'active':active,'configuration_recorded':configuration_recorded,'workspace':workspace,
            'registration':registration,'payload_root':str(payload),'components':components,
            'verified':True}

