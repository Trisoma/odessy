"""Application/launcher maintenance with prevalidated staging and recoverable activation."""
from __future__ import annotations
import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from installer.components import application
from installer.components import connection
from installer.components import model
from installer.components import ollama_runtime
from installer.core import pathing
from installer.windows import shelllink
from installer import workflow
from installer.core.checkpoint import fingerprint, read_state, save_state, writer_lock
from installer.core.download import DownloadError
from installer.core.manifest import _unique_keys
from installer.core.atomic import replace as replace_record
from installer.core.process import child_env, run_step
from installer.windows.session_process import ProcessTree, run_application
from installer.windows.shortcuts import digest, matches_launcher_arguments, user_folders
from installer.core.space import RESERVE_BYTES
from installer.operations.uninstall import _tree, removal_started, running_processes

JOURNAL='.installer/maintenance.json'
ACTIVE={'preparing','repairing','activating','recovery_required'}


def journal(root):
    path=root/JOURNAL;model.guard_path(path)
    if not path.exists():return None
    if path.stat().st_size>4*1024*1024:raise ValueError('Maintenance record is too large; stopped.')
    data=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=_unique_keys)
    # Records may spell the same directory long or short (8.3), so compare them as paths.
    recorded=data.get('install_dir') if isinstance(data,dict) else None
    old_state=data.get('old_state') if isinstance(data,dict) else None
    old_plan=old_state.get('plan') if isinstance(old_state,dict) else None
    recorded_old=old_plan.get('install_dir') if isinstance(old_plan,dict) else None
    if (not isinstance(data,dict) or not isinstance(recorded,str) or not pathing.same(recorded,root) or
        not re.fullmatch('[0-9a-f]{32}',str(data.get('id',''))) or
        data.get('status') not in ACTIVE|{'completed','rolled_back'} or
        not isinstance(old_state,dict) or fingerprint(old_state)!=data.get('old_state_sha256') or
        not isinstance(recorded_old,str) or not pathing.same(recorded_old,root)):
        raise ValueError('Maintenance record is corrupt; existing files were preserved.')
    return data


def require_idle(root):
    data=journal(root)
    if data and data['status'] in ACTIVE:raise ValueError('This installation has unfinished maintenance; open the repair window and restore the previous version first.')


def _save(root,data):
    path=root/JOURNAL;model.guard_path(path)
    temporary=path.with_name('.maintenance-'+uuid.uuid4().hex+'.tmp');model.guard_path(temporary)
    with temporary.open('x',encoding='utf-8') as stream:
        json.dump(data,stream,indent=2);stream.flush();os.fsync(stream.fileno())
    try:replace_record(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def launcher_record(root,executable,*,folders=None):
    folders=user_folders() if folders is None else folders
    target=root/'launcher/OdessyLauncher.exe'
    links=[];hashes={};retained=[]
    for folder in folders.values():
        path=folder/'Odessy.lnk';model.guard_path(path)
        if not path.exists():continue
        try:
            info=shelllink.read(path)
            if (not all(isinstance(info[key],str) and Path(info[key]).is_absolute() for key in ('target','working'))
                or not pathing.same(info['target'],target) or not matches_launcher_arguments(info['arguments'],root)
                or not pathing.same(info['working'],target.parent)):
                retained.append(str(path));continue
        except (OSError,ValueError,KeyError):retained.append(str(path));continue
        links.append(str(path));hashes[str(path)]=digest(path)
    return dict(schema_version=1,install_dir=str(root),executable=str(target),sha256=digest(executable),links=links,link_sha256=hashes),retained


def plan(root,bundle,executable,*,probe=running_processes,folders=None,validate_base=True):
    for path in (root,bundle,executable):model.guard_path(path)
    root=root.resolve();bundle=bundle.resolve();executable=executable.resolve()
    if removal_started(root):raise ValueError('An uninstall has started for this installation; choose a new directory to install.')
    require_idle(root)
    if root in Path(sys.executable).resolve().parents or root in executable.parents:
        raise ValueError('Run maintenance from the new OdessySetup.exe outside the installation directory.')
    if (root/'.git').exists() or (root/'pyproject.toml').exists():raise ValueError('A source checkout cannot be maintained.')
    state=read_state(root)
    if state['status']!='completed' or Path(state['plan']['install_dir']).resolve()!=root:
        raise ValueError('Maintenance requires a complete installation record at the original location.')
    if probe(root):raise ValueError("Close this installation's terminal, launcher panel and model service first.")
    if executable.suffix.lower()!='.exe':raise ValueError('Select the new standalone EXE.')
    with executable.open('rb') as stream:
        if stream.read(2)!=b'MZ':raise ValueError('Maintenance entry point is not a Windows EXE.')
    new=workflow.build_plan(root,Path(state['plan']['cache_dir']),bundle,state['plan']['endpoint'],state['plan']['timeout'])
    if new['model']!=state['plan']['model']:raise ValueError('This version changes the model; only application upgrades on the same model are supported for now.')
    if validate_base:
        for step in workflow.steps_for(new):
            if step.name in {'runtime','dependencies','ollama_runtime','model','configuration'}:
                if step.validate() is None:raise ValueError('Base component is missing and needs component repair: '+step.name)
    _,tool=application.validate_bundle(bundle)
    source=application.source_snapshot(application.REPO_ROOT)
    required=2*(sum(map(len,source.values()))+tool['unpacked_bytes']+executable.stat().st_size)+RESERVE_BYTES
    if shutil.disk_usage(root).free<required:raise ValueError('Not enough space to prepare the new version and keep rollback files.')
    inventory=[]
    for name in ('application','launcher'):inventory.extend(_tree(root,root/name))
    try:
        old=connection.inspect_application(root/'application')
        app_status='current' if old['source_sha256']=={name:application.text_hash(data) for name,data in source.items()} and old['tool_sha256']==tool['sha256'] else 'upgrade'
    except (OSError,ValueError,DownloadError):app_status='repair'
    links,retained=launcher_record(root,executable,folders=folders)
    result=dict(schema_version=1,install_dir=str(root),bundle=str(bundle),executable=str(executable),
        executable_sha256=digest(executable),state_sha256=fingerprint(state),new_plan=new,inventory=inventory,
        application_action=app_status,launcher=links,tool_archive_sha256=tool['sha256'],retained_shortcuts=retained,required_free_bytes=required,
        reuse=['runtime','dependencies','ollama-runtime','models','config'],scope='application-and-launcher-only')
    result['plan_sha256']=fingerprint(result)
    return result


def verify_staging(root,app,state_dir,plan):
    tree=ProcessTree()
    def popen(*args,**kwargs):
        process=subprocess.Popen(*args,**kwargs)
        try:tree.attach(process)
        except BaseException:process.terminate();process.wait(timeout=5);raise
        return process
    try:
        with ollama_runtime.managed_service(root/'ollama-runtime',root/'models',state_dir,plan['endpoint'],plan['model'],popen=popen,tree_factory=None):
            def run(command,**kwargs):
                # Use the same finite thinking/output allowance as the installed session.
                kwargs['env']=dict(kwargs['env'],ODESSY_MODEL_NUM_PREDICT='2048')
                return run_application(command,**kwargs)
            return connection.verify_connection(app,root/'models',plan['endpoint'],timeout=plan['timeout'],run=run)
    finally:tree.close()


def _signature(root,path):
    entries=_tree(root,path)
    prefix=path.relative_to(root)
    normalized=[]
    for entry in entries:
        value=dict(path=Path(entry['path']).relative_to(prefix).as_posix(),directory=entry['directory'],size=entry['size'])
        if not entry['directory']:value['mtime']=entry['mtime']
        normalized.append(value)
    return fingerprint(normalized)


def _components(data):
    names=data.get('components',['application','launcher'])
    if not isinstance(names,list) or not all(isinstance(name,str) for name in names) or len(names)>12 or len(set(names))!=len(names):raise ValueError('Maintenance component record is invalid.')
    for name in names:
        if name in {'application','launcher','runtime','dependencies','ollama-runtime'}:continue
        if not isinstance(name,str) or not re.fullmatch(r'models/(?:blobs/sha256-[0-9a-f]{64}|manifests/registry\.ollama\.ai/library/[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*)',name):
            raise ValueError('Maintenance component path escapes the installation.')
    return names


def _restore(root,data):
    folder=root/'.installer/updates'/data['id'];model.guard_path(folder)
    if data['status']=='preparing':
        data['status']='rolled_back';_save(root,data);return data
    if data['status']=='completed' and fingerprint(read_state(root))!=data.get('new_state_sha256'):
        raise ValueError('The installation record changed after upgrade; rollback stopped and files were preserved.')
    # Check all original copies before changing either component.
    for name in _components(data):
        target=root/name;backup=folder/('backup-'+name)
        original=backup if backup.exists() else target
        if data['had_'+name] and (not original.exists() or _signature(root,original)!=data['old_signatures'][name]):
            raise ValueError('Original component or rollback backup changed; cannot restore automatically: '+name)
    for name in _components(data):
        target=root/name;backup=folder/('backup-'+name);model.guard_path(target);model.guard_path(backup)
        if backup.exists():
            _tree(root,backup)
            if target.exists():
                _tree(root,target);discard=folder/('rejected-'+name.replace('/','_')+'-'+uuid.uuid4().hex)
                target.rename(discard)  # Preserve failed/new/user-changed files; no recursive deletion.
            target.parent.mkdir(parents=True,exist_ok=True);backup.rename(target)
        elif not data['had_'+name] and target.exists():
            _tree(root,target);target.rename(folder/('rejected-'+name.replace('/','_')+'-'+uuid.uuid4().hex))
        elif data['had_'+name] and not target.exists():raise ValueError('Both original component and backup are missing; cannot restore automatically: '+name)
    save_state(root,copy.deepcopy(data['old_state']))
    data['status']='rolled_back';_save(root,data);return data


def recover(root,*,probe=running_processes):
    model.guard_path(root);root=root.resolve()
    if (root/'.git').exists() or (root/'pyproject.toml').exists():raise ValueError('A source checkout cannot be rolled back.')
    if removal_started(root):raise ValueError('An installation being uninstalled cannot be rolled back.')
    if root in Path(sys.executable).resolve().parents:raise ValueError('Run rollback from outside the installation directory.')
    if probe(root):raise ValueError('Close all windows and services for this installation first.')
    with writer_lock(root):
        data=journal(root)
        if not data or data['status']=='rolled_back':raise ValueError('No maintenance record to roll back.')
        if data.get('rollback_discarded'):raise ValueError('The rollback backup for this maintenance was cleaned up; the previous version cannot be restored.')
        try:return _restore(root,data)
        except BaseException as error:
            data.update(status='recovery_required',error=str(error));_save(root,data);raise


def execute(root,bundle,executable,expected,*,probe=running_processes,folders=None,
            planner=plan,assemble=application.assemble,verify=verify_staging,final_check=connection.inspect_application,repair=None):
    preview=planner(root,bundle,executable,probe=probe,folders=folders)
    if preview['plan_sha256']!=expected:raise ValueError('Files or versions changed; check the maintenance plan again.')
    root=Path(preview['install_dir'])
    with writer_lock(root):
        preview=planner(root,bundle,executable,probe=probe,folders=folders)
        if preview['plan_sha256']!=expected:raise ValueError('Files or versions changed; check the maintenance plan again.')
        identity=uuid.uuid4().hex;folder=root/'.installer/updates'/identity;model.guard_path(folder);folder.mkdir(parents=True)
        data=dict(schema_version=1,id=identity,install_dir=str(root),status='preparing',error=None,
                  old_state=read_state(root),had_application=(root/'application').exists(),had_launcher=(root/'launcher').exists())
        data['components']=['application','launcher',*reversed(preview.get('repair_components',[]))]
        for name in _components(data):data['had_'+name]=(root/name).exists()
        data['old_state_sha256']=fingerprint(data['old_state'])
        data['old_signatures']={name:_signature(root,root/name) for name in _components(data)}
        _save(root,data)
        try:
            if repair is not None:repair(root,folder,preview,data)
            settings=assemble(root/'runtime',root/'dependencies',bundle,folder/'new-application')
            launch=folder/'new-launcher';launch.mkdir()
            shutil.copyfile(executable,launch/'OdessyLauncher.exe')
            if digest(launch/'OdessyLauncher.exe')!=preview['executable_sha256']:raise ValueError('The new EXE changed during copying.')
            (launch/'odessy-shortcuts.json').write_text(json.dumps(preview['launcher'],indent=2),encoding='utf-8')
            proof=verify(root,folder/'new-application',folder/'service',preview['new_plan'])
            if not proof.get('inference_verified') or not proof.get('tool_round_trip_verified'):raise ValueError('Live model and tool checks failed for the new version; the previous version was preserved.')
            now=[]
            for name in ('application','launcher'):now.extend(_tree(root,root/name))
            if now!=preview['inventory']:raise ValueError('Original application files changed while preparing the new version; check again.')
            data.update(status='activating',proof=proof);_save(root,data)
            for name in ('application','launcher'):
                target=root/name
                if target.exists():target.rename(folder/('backup-'+name))
                (folder/('new-'+name)).rename(target)
            final_check(root/'application')
            new_state=copy.deepcopy(data['old_state']);new_state['plan']=preview['new_plan'];new_state['plan_sha256']=fingerprint(new_state['plan'])
            for step in new_state['steps']:
                if step['name'] in data.get('base_results',{}):step['result']=data['base_results'][step['name']]
                if step['name']=='application':step['result']=dict(app_dir=str(root/'application'),python=settings['python'])
                if step['name']=='connection':step['result']=dict(proof,validated_before_activation=True)
                if step['name']=='tool_bundle':step['result']=dict(archive_sha256=preview['tool_archive_sha256'],maintenance=True)
            save_state(root,new_state)
            data.update(status='completed',new_state_sha256=fingerprint(read_state(root)));_save(root,data)
            return data
        except BaseException as error:
            data['error']=str(error)
            try:_restore(root,data)
            except BaseException as recovery_error:
                data.update(status='recovery_required',error=str(error)+'; rollback: '+str(recovery_error));_save(root,data)
            raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--install-dir',type=Path,required=True)
    parser.add_argument('--tool-bundle',type=Path);parser.add_argument('--executable',type=Path)
    parser.add_argument('--apply',action='store_true');parser.add_argument('--confirm');parser.add_argument('--rollback',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.rollback:result=recover(args.install_dir)
        else:
            if args.tool_bundle is None or args.executable is None:raise ValueError('Specify --tool-bundle and --executable.')
            if args.apply:
                if not args.confirm:raise ValueError('Check the plan first, then pass its fingerprint with --confirm.')
                result=execute(args.install_dir,args.tool_bundle,args.executable,args.confirm)
            else:result=plan(args.install_dir,args.tool_bundle,args.executable)
        print(json.dumps({key:value for key,value in result.items() if key not in {'inventory','old_state'}},indent=2,ensure_ascii=False));return 0
    except (OSError,ValueError,KeyError,DownloadError,subprocess.SubprocessError) as error:
        print('Maintenance did not finish: ',error);return 1


if __name__=='__main__':raise SystemExit(main())
