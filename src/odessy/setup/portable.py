"""Portable desktop context: read-only payload and reviewed external configuration.

No installer, service registration, model download, trust grant or tool execution
is performed here. Runtime execution remains in service_entry and existing policy.
"""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import sys
import uuid

from .contracts import SetupSelection, fields, public
from .maintenance import atomic, default_workspace, digest, guard, read_json, validated_workspace

MANIFEST = 'portable-manifest.json'
REQUIRED = ('runtime/bin/python3', 'desktop/odessy_desktop', 'node/bin/node',
            'link/odessy-link', 'monitor/odessy-monitor', 'tools/dist/src/server.js')


def payload():
    value = os.environ.get('ODESSY_PORTABLE_ROOT', '')
    if not value or not Path(value).is_absolute():
        raise ValueError('launch the portable application using ./Odessy')
    return guard(value)


def state_root():
    root = payload()
    value = os.environ.get('ODESSY_PORTABLE_STATE')
    state = guard(value or Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state')))/'odessy/portable')
    if state == root or state.is_relative_to(root):
        raise ValueError('portable configuration must remain outside the application bundle')
    if state == Path(state.anchor) or state == Path.home():
        raise ValueError('choose a dedicated portable state directory')
    return state


def _manifest():
    root = payload()
    path=root/MANIFEST
    if path.is_symlink() or path.stat().st_size > 32*1024*1024:
        raise ValueError('portable manifest exceeds limit or is linked')
    data=json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(data,dict) or data.get('schema_version') != 1 or data.get('platform') != 'linux'
            or data.get('architecture') not in ('x86_64','arm64')
            or not isinstance(data.get('files'),dict) or not 1 <= len(data['files']) <= 100000):
        raise ValueError('invalid portable manifest')
    for name, entry in data['files'].items():
        path = PurePosixPath(name)
        if (not isinstance(name,str) or not name or path.is_absolute() or '..' in path.parts
                or '\\' in name or ':' in name or not isinstance(entry,dict)):
            raise ValueError('invalid portable inventory path')
    return root, data


def _checked_file(root, name, entry):
    path = root/name
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.exists():
        raise ValueError('portable component missing or linked outside bundle: '+name)
    if 'link' in entry:
        if not path.is_symlink() or os.readlink(path) != entry['link']:
            raise ValueError('portable link checksum mismatch: '+name)
    elif not resolved.is_file() or not isinstance(entry.get('sha256'),str) or digest(resolved) != entry['sha256']:
        raise ValueError('portable checksum mismatch: '+name)
    return resolved


def verify():
    root, manifest = _manifest()
    for name, entry in manifest['files'].items():
        _checked_file(root,name,entry)
    return {'valid':True,'checked':len(manifest['files']),'portable':True}


def _configuration(state):
    path = state/'configuration.json'
    if not path.exists():
        return {'schema_version':1,'selection':public(SetupSelection(role='combined')),
                'workspace':str(default_workspace()),'revision':None}
    if path.stat().st_size > 65536:
        raise ValueError('portable configuration exceeds 64 KiB')
    data = read_json(path)
    if set(data) != {'schema_version','selection','workspace','revision'} or data['schema_version'] != 1:
        raise ValueError('invalid portable configuration')
    selection = SetupSelection.from_dict(data['selection'])
    if selection.role != 'combined':
        raise ValueError('portable desktop supports combined role only')
    validated_workspace(data['workspace'],payload())
    return data


def spec(state):
    state = guard(state)
    if state != state_root():
        raise ValueError('portable state identity changed')
    root, manifest = _manifest()
    for name in REQUIRED:
        if name not in manifest['files']:
            raise ValueError('portable bundle lacks required component: '+name)
        resolved=_checked_file(root,name,manifest['files'][name])
        target=resolved.relative_to(root).as_posix()
        if not resolved.is_file() or target not in manifest['files']:
            raise ValueError('portable executable target is not inventoried')
        _checked_file(root,target,manifest['files'][target])
    configuration = _configuration(state)
    selection = SetupSelection.from_dict(configuration['selection'])
    return {'schema_version':1,'portable':True,'root':str(state),'bundle_root':str(root),
            'installation_id':hashlib.sha256(str(state).encode()).hexdigest()[:32],
            'version':manifest['version'],'role':'combined','python':str((root/REQUIRED[0]).resolve()),
            'desktop':str(root/REQUIRED[1]),'workspace':configuration['workspace'],
            'workspace_exists':Path(configuration['workspace']).is_dir(),
            'selection':public(selection),'model_configured':selection.model is not None,
            'voice_enabled':selection.voice.enabled,'registration':'not applicable (portable)',
            'payload':None,'service_recorded':False,'ready':False,
            'configuration_source':'portable','config':str(state/'configuration.json')}


@contextlib.contextmanager
def _configuration_lock(state):
    state.mkdir(parents=True,exist_ok=True,mode=0o700)
    with guard(state/'.configuration.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise ValueError('portable configuration is busy; retry after inspection') from None
        try: yield
        finally:
            stream.seek(0)
            if os.name == 'nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def _review(data,state):
    info = spec(state)
    selected = SetupSelection.from_dict(data.get('selection'))
    local = SetupSelection(role='combined')
    if selected.role != 'combined' or (selected.platform,selected.architecture) != (local.platform,local.architecture):
        raise ValueError('portable configuration must match this machine and combined role')
    from .planning import build_plan
    plan = build_plan(selected)
    if selected.model is not None:
        from .configuration import environment_text
        environment_text(plan)
    workspace = str(validated_workspace(data.get('workspace') or info['workspace'],payload()))
    validated_workspace(workspace,state)
    reviewed = {'installation_id':info['installation_id'],'active':info['version'],
                'manifest':digest(payload()/MANIFEST),'previous':_configuration(state),
                'configuration':{'schema_version':1,'selection':public(selected),'workspace':workspace}}
    reviewed['plan_id'] = hashlib.sha256(json.dumps(reviewed,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return reviewed


def dispatch(parameters):
    data = fields(parameters, {'action','root','selection','workspace','confirm','log_cursor'},'portable configuration')
    state = state_root()
    if data.get('root') != str(state):
        raise ValueError('portable state identity changed')
    action = data.get('action')
    if action == 'installed-spec':return spec(state)
    if action == 'package-verify':return verify()
    if action == 'package-status':
        info = spec(state)
        return {'portable':True,'installation_format':'portable','active':info['version'],'versions':[info['version']],
                'status':'idle','registration':info['registration'],'service_recorded':False,
                'configuration':{'workspace':info['workspace'],'role':info['role'],'model_configured':info['model_configured']}}
    if action == 'package-configure-review':return _review(data,state)
    if action == 'package-configure':
        with _configuration_lock(state):
            review = _review(data,state)
            if data.get('confirm') != review['plan_id']:
                raise ValueError('review the exact portable configuration and current state before applying')
            configuration = review['configuration'] | {'revision':uuid.uuid4().hex}
            atomic(state/'configuration.json',configuration)
            return {'configured':True,'model_configured':configuration['selection'].get('model') is not None,
                    'role':'combined','workspace':configuration['workspace'],'ready':False}
    raise ValueError('operation not supported for a portable bundle; close Odessy and remove the extracted folder to remove its binaries')


def environment():
    root = payload()
    os.environ['PATH'] = str(root/'node/bin') + os.pathsep + str(root/'runtime/bin') + os.pathsep + os.environ.get('PATH','')
    os.environ['ODESSY_LINK_BIN'] = str(root/'link/odessy-link')
    os.environ['ODESSY_MONITOR_BIN'] = str(root/'monitor/odessy-monitor')
    os.environ['ODESSY_NODE_TOOLS_DIR'] = str(root/'tools')
    os.environ.setdefault('ODESSY_MONITOR_STATE_DIR',str(state_root()/'monitoring'))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Launch the relocatable Odessy Linux preview; no installer or model download.')
    parser.add_argument('--verify',action='store_true')
    parser.add_argument('--diagnose',action='store_true')
    args = parser.parse_args(argv)
    try:
        if sys.platform != 'linux':raise ValueError('this archive requires Linux')
        root, manifest = _manifest()
        arch = {'aarch64':'arm64','amd64':'x86_64'}.get(platform.machine().lower(),platform.machine().lower())
        if arch != manifest['architecture']:raise ValueError('bundle architecture does not match this machine')
        if args.verify:
            print(json.dumps(verify()));return 0
        info = spec(state_root())
        if args.diagnose:
            print(json.dumps({'platform':sys.platform,'architecture':arch,'version':info['version'],
                              'model_configured':info['model_configured'],'state':info['root']}));return 0
        if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
            raise ValueError('a graphical Linux desktop session is required; use --diagnose for headless checks')
        environment()
        os.execv(info['desktop'],[info['desktop'],'--installed','--install-root',info['root']])
    except (ValueError,OSError,KeyError) as error:
        print('Odessy portable: '+str(error),file=sys.stderr)
        return 2

if __name__ == '__main__':raise SystemExit(main())
