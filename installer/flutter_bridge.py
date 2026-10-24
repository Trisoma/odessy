"""Local Flutter adapter for the established installer workflow/checkpoint engine."""
from __future__ import annotations
import json
import contextlib
import io
import signal
import threading
from pathlib import Path
from installer import role_aware,workflow
from installer.core.checkpoint import fingerprint,read_state,state_path
from odessy.setup.cli import _load
from odessy.setup.contracts import SetupSelection,fields


def execute(parameters,*,cancelled=lambda:False):
    data=fields(parameters,{'action','root','cache','bundle','config','role','confirm','resume'},'installer request')
    action=data.get('action')
    if action not in ('review','install','resume','status'):raise ValueError('unsupported installer engine action')
    if not isinstance(data.get('root'),str) or not data['root'].strip():raise ValueError('installation directory required')
    root=Path(data['root']).absolute();workflow.model.guard_path(root)
    if action=='status':
        state=read_state(root)
        return {'engine':'installer.workflow','status':state['status'],'summary':state['summary'],'stages':state['steps'],'selection':state['plan'].get('selection'),'plan_id':fingerprint(state),'ready':False}
    resume=action=='resume' or data.get('resume') is True
    if data.get('resume') not in (None,True,False):raise ValueError('invalid resume mode')
    previous=read_state(root) if state_path(root).exists() else None
    if previous and not resume:raise ValueError('existing checkpoint requires explicit resume')
    if resume and not previous:raise ValueError('no checkpoint to resume')
    if previous:
        selection=SetupSelection.from_dict(previous['plan']['selection'])
        cache=previous['plan']['cache_dir'];bundle=previous['plan']['tool_bundle']
        for key,expected in (('cache',cache),('bundle',bundle)):
            if data.get(key) and Path(data[key]).resolve()!=Path(expected).resolve():raise ValueError('resume paths differ from checkpoint')
        if data.get('config') or data.get('role'):raise ValueError('resume uses stored selection, not new overrides')
    else:
        if not data.get('cache') or not data.get('bundle'):raise ValueError('cache and prebuilt tool bundle required')
        selection=_load(data['config']) if data.get('config') else SetupSelection(role=data.get('role','combined'))
        cache,bundle=data['cache'],data['bundle']
    plan=role_aware.build_plan(root,cache,bundle,selection)
    preview={'engine':'installer.workflow','plan':plan,'resume':resume,'volumes':workflow.combined_budget(plan),'model_downloads':False,'stages':['tool_bundle','runtime','dependencies','application','configuration'],'ready':False}
    preview['plan_id']=fingerprint({'plan':plan,'resume':resume,'checkpoint':fingerprint(previous) if previous else None})
    if action=='review':return preview
    if data.get('confirm')!=preview['plan_id']:raise ValueError('review exact installer engine plan before confirming')
    event=threading.Event();old={}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM,signal.SIGINT):old[sig]=signal.signal(sig,lambda *_:event.set())
    try:
        class DiscardLog(io.TextIOBase):
            def write(self,value):return len(value)
            def flush(self):pass
        with contextlib.redirect_stdout(DiscardLog()),contextlib.redirect_stderr(DiscardLog()):
            state=role_aware.execute(plan,resume=resume,cancelled=lambda:event.is_set() or cancelled())
        return {'engine':'installer.workflow','status':state['status'],'summary':state['summary'],'stages':state['steps'],'model_downloads':False,'ready':False}
    finally:
        for sig,handler in old.items():signal.signal(sig,handler)
