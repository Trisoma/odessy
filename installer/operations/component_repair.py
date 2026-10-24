"""Repair pinned private components at their fixed paths with maintenance rollback."""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
from pathlib import Path
from installer.components import application
from installer.components import dependencies
from installer.operations import maintenance
from installer.components import model
from installer.components import ollama_runtime
from installer.components import runtime
from installer import workflow
from installer.core.checkpoint import fingerprint
from installer.core.download import DownloadError,download_artifact,verified
from installer.core.process import child_env,run_step
from installer.core.space import load_resources,volume_for,RESERVE_BYTES

DEPENDENCY_CHECK = r"""
import base64,csv,hashlib,importlib.metadata as m,io,json,pathlib,sys
root=pathlib.Path(sys.prefix).resolve();bad=[];checked=0
for name in json.loads(sys.argv[1]):
 d=m.distribution(name);record=d.read_text('RECORD')
 # Distribution.files filters out missing files in current Python releases.
 # Read the original RECORD so a deleted file cannot disappear from the audit.
 if not record:bad.append(name+':missing RECORD');continue
 for row in csv.reader(io.StringIO(record)):
  if len(row)!=3 or not row[0]:bad.append(name+':invalid RECORD');continue
  relative,expected,size=row;p=pathlib.Path(d.locate_file(relative));resolved=p.resolve()
  if root not in resolved.parents or not p.is_file():bad.append(name+':'+relative);continue
  if expected:
   mode,sep,value=expected.partition('=')
   if mode!='sha256' or not sep:bad.append(name+':unsupported hash');continue
   if size and p.stat().st_size!=int(size):bad.append(name+':'+relative);continue
   actual=base64.urlsafe_b64encode(hashlib.sha256(p.read_bytes()).digest()).rstrip(b'=').decode()
   if actual!=value:bad.append(name+':'+relative)
   checked+=1
print(json.dumps(dict(checked=checked,bad=bad[:12])))
"""


def dependency_integrity(root):
    manifest,_=load_resources(dependencies.DEFAULT_RESOURCES)
    python=root/'dependencies/venv/Scripts/python.exe'
    output=run_step([str(python),'-I','-B','-c',DEPENDENCY_CHECK,json.dumps([a.id for a in manifest.artifacts])],
        cwd=root,env=child_env(root/'runtime'),label='Installed wheel file integrity',timeout=180)
    value=json.loads(output)
    if value['bad']:raise ValueError('Dependency file missing or failed verification: '+', '.join(value['bad']))
    return value


def model_targets(root):
    manifest,resources,raw=model.load_release()
    bad=[]
    for artifact in manifest.artifacts:
        path=model.blob_path(root/'models',artifact);model.guard_path(path)
        if path.exists() and not path.is_file():raise ValueError('A directory exists where the model file belongs; preserved.')
        if not verified(path,artifact):bad.append(path.relative_to(root).as_posix())
    path=model.manifest_path(root/'models',resources['model']);model.guard_path(path)
    if path.exists() and not path.is_file():raise ValueError('A directory exists where the model manifest belongs; preserved.')
    if not path.exists() or path.read_bytes()!=raw:bad.append(path.relative_to(root).as_posix())
    return bad


def _health(root,new):
    if new.get('engine')=='role-aware-existing-installer-v1':
        from installer.operations.role_maintenance import health
        return health(root,new)
    repairs=[];issues={}
    validators={step.name:step.validate for step in workflow.steps_for(new)}
    for name in ('runtime','dependencies','ollama_runtime'):
        try:
            if validators[name]() is None:raise ValueError('Component missing')
            if name=='runtime':
                run_step([str(root/'runtime/python-runtime/python.exe'),'-I','-B','-c','import venv,ensurepip,ssl,importlib.metadata,zipfile'],
                    cwd=root,env=child_env(root/'runtime'),label='Private Python standard library')
            if name=='dependencies':dependency_integrity(root)
        except (OSError,ValueError,DownloadError,subprocess.TimeoutExpired) as error:
            directory='ollama-runtime' if name=='ollama_runtime' else name
            repairs.append(directory);issues[directory]=str(error)
    if 'runtime' in repairs and 'dependencies' not in repairs:
        repairs.append('dependencies')
        issues['dependencies']=' The pinned-path virtual environment is rebuilt with the runtime.'
    # User configuration is never silently replaced by component repair.
    if validators['configuration']() is None:raise ValueError('Connection configuration is missing; this module does not overwrite or rebuild it.')
    repairs.extend(model_targets(root))
    return repairs,issues


def _artifacts(preview):
    names=set(preview['repair_components']);values=[]
    if 'runtime' in names:values.extend(runtime.load_release(runtime.DEFAULT_RESOURCES)[0].artifacts)
    if 'dependencies' in names:values.extend(load_resources(dependencies.DEFAULT_RESOURCES)[0].artifacts)
    if 'ollama-runtime' in names:values.extend(ollama_runtime.load_release()[0].artifacts)
    if preview.get('new_plan',{}).get('engine')=='role-aware-existing-installer-v1':return values
    manifest,_,_=model.load_release()
    root=Path(preview['install_dir'])
    values.extend(a for a in manifest.artifacts if model.blob_path(root/'models',a).relative_to(root).as_posix() in names)
    return values


def plan(root,bundle,executable,*,probe=maintenance.running_processes,folders=None):
    value=maintenance.plan(root,bundle,executable,probe=probe,folders=folders,validate_base=False)
    root=Path(value['install_dir']);repairs,issues=_health(root,value['new_plan'])
    repairs=sorted(repairs,key=lambda name:({'runtime':0,'dependencies':1,'ollama-runtime':2}.get(name,3),name))
    value.update(repair_components=repairs,component_issues=issues,base_inventory=[],scope='pinned-components-application-and-launcher')
    for name in repairs:value['base_inventory'].extend(maintenance._tree(root,root/name))
    extra=0
    if 'runtime' in repairs:extra+=sum(item['unpacked_bytes'] for item in runtime.load_release(runtime.DEFAULT_RESOURCES)[1]['runtimes'].values())
    if 'dependencies' in repairs:extra+=sum(item['unpacked_bytes'] for item in load_resources(dependencies.DEFAULT_RESOURCES)[1]['layout'].values())
    if 'ollama-runtime' in repairs:extra+=ollama_runtime.load_release()[1]['unpacked_bytes']
    extra+=sum(a.size_bytes for a in _artifacts(value) if a.id.startswith('model-'))
    cache=Path(value['new_plan']['cache_dir']);groups={}
    amounts=[(root,value['required_free_bytes']+2*extra),(cache,sum(a.size_bytes for a in _artifacts(value)))]
    for path,amount in amounts:
        volume=volume_for(path);item=groups.setdefault(volume.id,dict(volume=volume.id,free_bytes=volume.free_bytes,required_bytes=0))
        item['free_bytes']=min(item['free_bytes'],volume.free_bytes);item['required_bytes']+=amount
    for item in groups.values():
        item['required_bytes']+=max(RESERVE_BYTES,item['required_bytes']//10)
        if item['free_bytes']<item['required_bytes']:raise ValueError('Not enough space to repair while keeping the original components and preparing verified resources.')
    value['space_budget']=list(groups.values());value['required_free_bytes']+=2*extra
    value.pop('plan_sha256',None)
    # Available space is checked on every preview, including immediately before
    # execution, but unrelated disk writes must not invalidate user confirmation.
    stable=dict(value,space_budget=[
        {key:item for key,item in group.items() if key!='free_bytes'}
        for group in value['space_budget']])
    value['plan_sha256']=fingerprint(stable)
    return value


def repair(root,folder,preview,data,*,download=download_artifact):
    cache=Path(preview['new_plan']['cache_dir'])
    # Finish network transfers before moving any installed component.
    for artifact in _artifacts(preview):
        print('Preparing repair download:',artifact.id,flush=True);download(artifact,cache)
    now=[]
    for name in preview['repair_components']:now.extend(maintenance._tree(root,root/name))
    if now!=preview['base_inventory']:raise ValueError('Components to repair changed during download; check again.')
    if not preview['repair_components']:return
    data['status']='repairing';maintenance._save(root,data)
    reports={}
    def backup(name):
        target=root/name;model.guard_path(target)
        if target.exists():
            destination=folder/('backup-'+name);model.guard_path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
            target.rename(destination)
    for name in preview['repair_components']:
        if name.startswith('models/'):continue
        backup(name)
        if name=='runtime':
            manifest,resources=runtime.load_release(runtime.DEFAULT_RESOURCES)
            reports['runtime']=runtime.prepare_runtime(manifest,resources,root/name,cache,download=download)
        elif name=='dependencies':reports['dependencies']=dependencies.prepare_dependencies(root/'runtime',root/name,cache,download=download)
        elif name=='ollama-runtime':reports['ollama_runtime']=ollama_runtime.prepare_runtime(root/name,cache,download=download)
    if preview['new_plan'].get('engine')=='role-aware-existing-installer-v1':
        if 'dependencies' in preview['repair_components']:dependency_integrity(root)
        data['base_results']=reports;maintenance._save(root,data);return
    manifest,resources,raw=model.load_release()
    for name in preview['repair_components']:
        if name.startswith('models/'):backup(name)
    for artifact in manifest.artifacts:
        name=model.blob_path(root/'models',artifact).relative_to(root).as_posix()
        if name in preview['repair_components']:
            model.install_blob(download(artifact,cache).path,root/name,artifact)
    path=model.manifest_path(root/'models',resources['model'])
    if path.relative_to(root).as_posix() in preview['repair_components']:
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.repair-',delete=False) as stream:
                temporary=Path(stream.name);stream.write(raw);stream.flush();os.fsync(stream.fileno())
            model.publish_new(temporary,path)
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
    if any(name.startswith('models/') for name in preview['repair_components']):reports['model']=model.verify_model(root/'models')
    if 'dependencies' in preview['repair_components']:dependency_integrity(root)
    data['base_results']=reports;maintenance._save(root,data)


def execute(root,bundle,executable,expected,**kwargs):
    return maintenance.execute(root,bundle,executable,expected,planner=plan,repair=repair,**kwargs)


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir',type=Path,required=True)
    parser.add_argument('--tool-bundle',type=Path,required=True)
    parser.add_argument('--executable',type=Path,required=True)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--confirm')
    args=parser.parse_args(argv)
    try:
        if args.apply:
            if not args.confirm:raise ValueError('Inspect the plan first and supply --confirm plan_sha256.')
            value=execute(args.install_dir,args.tool_bundle,args.executable,args.confirm)
        else:value=plan(args.install_dir,args.tool_bundle,args.executable)
        print(json.dumps({key:item for key,item in value.items() if key not in {'inventory','base_inventory','old_state'}},indent=2,ensure_ascii=False))
        return 0
    except (OSError,ValueError,KeyError,DownloadError,subprocess.SubprocessError) as error:
        print('Component repair incomplete:',error);return 1


if __name__=='__main__':raise SystemExit(main())
