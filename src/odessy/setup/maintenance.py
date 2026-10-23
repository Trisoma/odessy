"""Offline, user-scoped runtime installation with exact ownership and rollback.

Bundles contain wheel files and a hash/license manifest. No model or remote URL is
accepted. Version directories never move: virtualenv absolute paths stay valid.
"""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import signal
import sys
import tempfile
import time
import uuid
from .contracts import SetupSelection, public

class Cancelled(RuntimeError):
    pass

def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else _digest(stream)

def _digest(stream):
    h=hashlib.sha256()
    for block in iter(lambda:stream.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def guard(path):
    path=Path(path).absolute()
    for item in (path,*path.parents):
        if item.is_symlink() or (hasattr(item,'is_junction') and item.is_junction()):
            raise ValueError('linked installation paths are not supported')
        if os.name=='nt' and item.exists() and getattr(item.lstat(),'st_file_attributes',0)&1024:
            raise ValueError('reparse points are not supported')
    return path

def atomic(path,data):
    path=guard(path)
    fd,name=tempfile.mkstemp(prefix='.setup-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(data,stream,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)

def read_json(path):
    guard(path)
    with Path(path).open('rb') as stream: raw=stream.read(4*1024*1024+1)
    if len(raw)>4*1024*1024: raise ValueError('setup record exceeds limit')
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result: raise ValueError('duplicate record field')
            result[k]=v
        return result
    return json.loads(raw,object_pairs_hook=unique)

def run(argv,*,cancelled=lambda:False):
    # No shell and no inherited stdin. Output is not persisted (may contain keys).
    process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        **({'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}))
    try:
        deadline=time.monotonic()+600
        while process.poll() is None:
            if cancelled(): raise Cancelled('operation cancelled')
            if time.monotonic()>deadline: raise RuntimeError('setup child timed out')
            time.sleep(.05)
        if process.returncode: raise RuntimeError('setup child failed; previous installation remains active')
    finally:
        if process.poll() is None:
            if os.name=='nt':
                subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
            else:
                try: os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError: pass
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if os.name!='nt':
                    try: os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                process.kill();process.wait()

class ManagedInstallation:
    def __init__(self,root,*,runner=run,runtime_source=None):
        self.root=guard(root)
        if self.root==Path(self.root.anchor) or self.root==Path.home(): raise ValueError('choose a dedicated installation directory')
        self.runner=runner
        self.runtime_source=guard(runtime_source) if runtime_source else None

    def record(self):
        data=read_json(self.root/'installation.json')
        if (not isinstance(data,dict) or data.get('schema_version')!=1 or data.get('root')!=str(self.root)
            or not re.fullmatch('[a-f0-9]{32}',str(data.get('id',''))) or not isinstance(data.get('versions'),dict)):
            raise ValueError('invalid ownership record')
        for version,files in data['versions'].items():
            if not re.fullmatch('[a-f0-9]{32}',version) or not isinstance(files,dict): raise ValueError('invalid version record')
            for name,sha in files.items():
                relative=Path(name)
                if relative.is_absolute() or '..' in relative.parts or ':' in name or '\\' in name or not re.fullmatch('[a-f0-9]{64}',str(sha)):
                    raise ValueError('invalid owned file record')
        if data.get('active') not in data['versions'] and data.get('active') is not None: raise ValueError('invalid active version')
        return data

    def identity(self): return self.record()['id']

    @contextlib.contextmanager
    def lock(self):
        guard(self.root)
        if not self.root.exists(): self.root.mkdir(parents=True)
        if not (self.root/'installation.json').exists() and any(p.name!='.setup.lock' for p in self.root.iterdir()):
            raise ValueError('directory is not an owned empty installation')
        lock=guard(self.root/'.setup.lock')
        with lock.open('a+b') as stream:
            stream.seek(0,2)
            if not stream.tell(): stream.write(b'0');stream.flush()
            stream.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError: raise RuntimeError('installation busy') from None
            try: yield
            finally:
                stream.seek(0)
                if os.name=='nt': msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else: fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

    def plan(self,bundle,selection):
        bundle=guard(bundle);manifest=read_json(bundle/'manifest.json')
        if set(manifest)!={'schema_version','artifacts'} or manifest['schema_version']!=1 or not isinstance(manifest['artifacts'],list) or not 1<=len(manifest['artifacts'])<=256:
            raise ValueError('invalid offline bundle manifest')
        seen=set()
        for artifact in manifest['artifacts']:
            if not isinstance(artifact,dict) or set(artifact)!={'file','sha256','license'}: raise ValueError('invalid artifact')
            name=artifact['file']
            if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.+-]+\.whl',name) or name in seen: raise ValueError('invalid wheel filename')
            seen.add(name)
            if not isinstance(artifact['license'],str) or not artifact['license'].strip(): raise ValueError('artifact license required')
            if not re.fullmatch('[a-f0-9]{64}',str(artifact['sha256'])) or digest(guard(bundle/name))!=artifact['sha256']: raise ValueError('artifact checksum mismatch')
        if not any(name.lower().startswith('odessy-') for name in seen): raise ValueError('bundle lacks Odessy wheel')
        local=SetupSelection(role=selection.role)
        if (local.platform,local.architecture)!=(selection.platform,selection.architecture): raise ValueError('bundle must be applied on its selected platform')
        state=self.record() if (self.root/'installation.json').exists() else None
        data={'previous_state':state,'schema_version':1,'root':str(self.root),'bundle':str(bundle),'manifest':manifest,'selection':public(selection),'downloads':[],'service_changes':[]}
        if self.runtime_source:
            from .runtime import inventory
            data['python_runtime']=inventory(self.runtime_source)
            data['python_runtime_source']=str(self.runtime_source)
        data['plan_id']=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return data

    def install(self,bundle,selection,confirm,*,cancelled=lambda:False):
        plan=self.plan(bundle,selection)
        if confirm!=plan['plan_id']: raise ValueError('review the exact installation plan before applying')
        with self.lock():
            if self.plan(bundle,selection)['plan_id']!=confirm: raise ValueError('installation changed after review')
            if (self.root/'service.json').exists(): raise ValueError('remove owned service before changing the active runtime')
            if cancelled(): raise Cancelled('operation cancelled')
            record=self.record() if (self.root/'installation.json').exists() else {'schema_version':1,'id':uuid.uuid4().hex,'root':str(self.root),'active':None,'versions':{}}
            # Persist ownership before any large effect. Interrupted versions remain inspectable.
            version=uuid.uuid4().hex
            target=guard(self.root/'versions'/version);target.mkdir(parents=True)
            atomic(self.root/'installation.json',record)
            journal={'schema_version':1,'version':version,'status':'preparing','previous':record['active']}
            atomic(self.root/'operation.json',journal)
            try:
                python_builder=sys.executable
                if self.runtime_source:
                    from .runtime import copy_owned
                    runtime_key=hashlib.sha256(json.dumps(plan['python_runtime'],sort_keys=True).encode()).hexdigest()[:32]
                    python_builder=str(copy_owned(self.runtime_source,guard(self.root/'runtimes'/runtime_key),plan['python_runtime']))
                    record.setdefault('runtimes',{})[runtime_key]=plan['python_runtime']['files']
                    atomic(self.root/'installation.json',record)
                self.runner([python_builder,'-m','venv','--copies',str(target)],cancelled=cancelled)
                python=target/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
                # Recheck artifacts immediately before pip; no index or dependency resolution.
                if self.plan(bundle,selection)['manifest']!=plan['manifest']: raise ValueError('bundle changed after review')
                wheels=[str(Path(bundle)/a['file']) for a in plan['manifest']['artifacts']]
                for argv in ([str(python),'-I','-m','pip','install','--no-index','--no-deps',*wheels], [str(python),'-I','-m','pip','check'],[str(python),'-I','-m','odessy','setup','--role',selection.role,'--non-interactive'],[str(python),'-I','-m','odessy','--help']):
                    if cancelled(): raise Cancelled('operation cancelled')
                    self.runner(argv,cancelled=cancelled)
                (target/'selection.json').write_text(json.dumps({'schema_version':1,'selection':public(selection)},indent=2),encoding='utf-8')
                if cancelled(): raise Cancelled('operation cancelled')
                files={p.relative_to(target).as_posix():digest(guard(p)) for p in target.rglob('*') if p.is_file()}
                record['versions'][version]=files;record['active']=version;record['selection']=public(selection)
                atomic(self.root/'installation.json',record)
                journal['status']='completed';atomic(self.root/'operation.json',journal)
            except BaseException:
                journal['status']='interrupted';atomic(self.root/'operation.json',journal)
                # Do not recursively delete partial or changed files. Old active version stays selected.
                raise
            return {'installed':True,'active':version,'service_started':False,'ready':False,'role':selection.role}

    def verify(self):
        record=self.record();version=record['active']
        if version is None: return {'valid':False,'issues':['no active installation'],'ready':False}
        base=self.root/'versions'/version;issues=[]
        for name,sha in record['versions'][version].items():
            path=guard(base/name)
            if not path.is_file() or digest(path)!=sha: issues.append(name)
        return {'valid':not issues,'issues':issues,'ready':False,'active':version}

    def repair(self,bundle,*,confirm,cancelled=lambda:False):
        selection=SetupSelection.from_dict(self.record()['selection'])
        return self.install(bundle,selection,confirm,cancelled=cancelled)

    def rollback(self,version,*,confirm):
        with self.lock():
            record=self.record()
            if (self.root/'service.json').exists(): raise ValueError('remove owned service before rollback')
            if confirm!=record['id'] or version not in record['versions']: raise ValueError('unknown owned version or confirmation')
            for name,sha in record['versions'][version].items():
                path=guard(self.root/'versions'/version/name)
                if not path.is_file() or digest(path)!=sha: raise ValueError('rollback version has changed')
            record['active']=version
            stored=read_json(self.root/'versions'/version/'selection.json')
            record['selection']=public(SetupSelection.from_dict(stored['selection']))
            atomic(self.root/'installation.json',record)
            return {'active':version,'service_started':False}

    def uninstall(self,*,confirm):
        if Path(sys.executable).resolve().is_relative_to(self.root): raise ValueError('run uninstall from the external setup bootstrap')
        with self.lock():
            record=self.record()
            if confirm!=record['id']: raise ValueError('confirm exact installation identity')
            if (self.root/'service.json').exists(): raise ValueError('remove the owned service before uninstalling runtime files')
            removed=[];retained=[]
            for version,files in record['versions'].items():
                base=self.root/'versions'/version
                for name,sha in files.items():
                    path=guard(base/name)
                    if path.is_file() and digest(path)==sha: path.unlink();removed.append(f'{version}/{name}')
                    elif path.exists(): retained.append(f'{version}/{name}')
            for runtime_id,files in record.get('runtimes',{}).items():
                if not re.fullmatch('[a-f0-9]{32}',runtime_id):raise ValueError('invalid runtime ownership identity')
                for name,sha in files.items():
                    relative=Path(name)
                    if relative.is_absolute() or '..' in relative.parts or ':' in name or chr(92) in name or not re.fullmatch('[a-f0-9]{64}',str(sha)):raise ValueError('invalid runtime ownership path')
                    path=guard(self.root/'runtimes'/runtime_id/name)
                    if path.is_file() and digest(path)==sha:path.unlink();removed.append('runtimes/'+runtime_id+'/'+name)
                    elif path.exists():retained.append('runtimes/'+runtime_id+'/'+name)
            record['active']=None;atomic(self.root/'installation.json',record)
            return {'removed':removed,'retained':retained,'models_removed':False,'settings_removed':False}
