"""Independent component edits over the existing setup persistence engines."""
from __future__ import annotations
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from .contracts import SetupSelection, fields, public

SECTIONS = {
    'llm': {'provider','name','endpoint','credential_env'},
    'stt': {'stt_provider','stt_executable','stt_model','whisper_bin','whisper_model'},
    'tts': {'tts_provider','tts_executable','tts_model','kokoro_model','kokoro_voices'},
    'voice': {'enabled'},
    'host': {'role','workspace'},
}

def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def _load(root):
    if root:
        from .native_bridge import manage
        spec=manage({'action':'installed-spec','root':root})
    else:
        from .configured import resume
        spec=resume()
    selection=public(SetupSelection.from_dict(spec.get('selection') or {'role':'combined'}))
    value={'selection':selection,'workspace':spec.get('workspace',''),'root':root,
           'identity':spec.get('installation_id') or spec.get('config',''),
           'version':spec.get('version'),'sha256':spec.get('sha256')}
    return dict(value,revision=_digest(value),restart_required=False)

def _review(data):
    current=_load(data.get('root',''))
    if data.get('revision')!=current['revision']:
        raise ValueError('Configuration changed. Reload this section before saving.')
    section=data.get('section')
    if section not in SECTIONS:raise ValueError('unknown configuration section')
    patch=fields(data.get('patch'),SECTIONS[section],'component fields')
    selected=json.loads(json.dumps(current['selection']))
    workspace=current['workspace']
    if section=='llm':
        selected['model']=patch if patch else None
    elif section=='host':
        if 'role' in patch:selected['role']=patch['role']
        if 'workspace' in patch:
            from .maintenance import validated_workspace
            workspace=str(validated_workspace(patch['workspace'],Path(data.get('root') or __file__).resolve()))
    else:
        selected['voice'].update(patch)
    selection=SetupSelection.from_dict(selected)
    # Validate only this component's supplied local assets; other components may
    # intentionally be incomplete. Never import or execute custom provider code.
    path_keys={'stt':{'whisper_bin','whisper_model','stt_executable','stt_model'},
               'tts':{'kokoro_model','kokoro_voices','tts_executable','tts_model'}}
    for key in path_keys.get(section,set()):
        value=patch.get(key)
        if value and not Path(value).expanduser().is_file():
            raise ValueError(f'{key}: choose an existing file')
    value={'selection':public(selection),'workspace':workspace,'revision':current['revision'],
           'root':current['root'],'section':section,'restart_required':True}
    if current['root']:
        from .native_bridge import manage
        value['engine_plan']=manage({'action':'package-configure-review','root':current['root'],
             'selection':value['selection'],'workspace':workspace})['plan_id']
    if _load(current['root'])['revision'] != current['revision']:
        raise ValueError('Configuration changed during review. Reload before saving.')
    return dict(value,plan_id=_digest(value))

@contextmanager
def _source_lock():
    from .configured import desktop_state_file
    parent=desktop_state_file().parent
    parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock=parent/'.component-settings.lock'
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:raise ValueError('Settings are being saved, or an interrupted writer needs inspection.') from None
    try:yield
    finally:os.close(fd);lock.unlink()

def dispatch(parameters):
    data=fields(parameters,{'action','root','section','patch','revision','confirm'},'component settings')
    root=data.get('root','')
    if not isinstance(root,str):raise ValueError('invalid installation root')
    action=data.get('action','load')
    if action=='load':return _load(root)
    if action=='review':return _review(data)
    if action!='save':raise ValueError('unsupported component operation')
    def save():
        reviewed=_review(data)
        if data.get('confirm')!=reviewed['plan_id']:raise ValueError('Review the exact section changes before saving.')
        if root:
            from .native_bridge import manage
            manage({'action':'package-configure','root':root,'selection':reviewed['selection'],
                    'workspace':reviewed['workspace'],'confirm':reviewed['engine_plan']})
        else:
            from .configured import desktop_state_file,saved_spec,remember
            from .configuration import export_selection
            from .planning import build_plan
            selection=SetupSelection.from_dict(reviewed['selection'])
            output=desktop_state_file().parent/f'components-{uuid.uuid4().hex}.json'
            export_selection(build_plan(selection),output)
            spec=saved_spec(selection,output,'selection')
            if reviewed['workspace']:spec['workspace']=reviewed['workspace']
            remember(spec)
        return dict(_load(root),saved=True,restart_required=True)
    if root:return save()  # Existing engine owns locking and exact review checks.
    with _source_lock():return save()
