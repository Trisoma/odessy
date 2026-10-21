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
from installer import application, connection, model, ollama_runtime, workflow
from installer.checkpoint import fingerprint, read_state, save_state, writer_lock
from installer.download import DownloadError
from installer.manifest import _unique_keys
from installer.process import child_env, run_step
from installer.session_process import ProcessTree, run_application
from installer.shortcuts import digest, powershell, user_folders
from installer.space import RESERVE_BYTES
from installer.uninstall import _tree, removal_started, running_processes

JOURNAL='.installer/maintenance.json'
ACTIVE={'preparing','repairing','activating','recovery_required'}


def journal(root):
    path=root/JOURNAL;model.guard_path(path)
    if not path.exists():return None
    if path.stat().st_size>4*1024*1024:raise ValueError('维护记录过大，已停止。')
    data=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=_unique_keys)
    if (not isinstance(data,dict) or data.get('install_dir')!=str(root.resolve()) or
        not re.fullmatch('[0-9a-f]{32}',str(data.get('id',''))) or
        data.get('status') not in ACTIVE|{'completed','rolled_back'} or
        not isinstance(data.get('old_state'),dict) or fingerprint(data['old_state'])!=data.get('old_state_sha256') or
        data['old_state'].get('plan',{}).get('install_dir')!=str(root.resolve())):
        raise ValueError('维护记录损坏，已保留现有文件。')
    return data


def require_idle(root):
    data=journal(root)
    if data and data['status'] in ACTIVE:raise ValueError('此安装有未结束的维护，请先打开修复窗口恢复原版本。')


def _save(root,data):
    path=root/JOURNAL;model.guard_path(path)
    temporary=path.with_name('.maintenance-'+uuid.uuid4().hex+'.tmp');model.guard_path(temporary)
    with temporary.open('x',encoding='utf-8') as stream:
        json.dump(data,stream,indent=2);stream.flush();os.fsync(stream.fileno())
    try:os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def launcher_record(root,executable,*,folders=None):
    folders=user_folders() if folders is None else folders
    target=root/'launcher/OdessyLauncher.exe';arguments=subprocess.list2cmdline(['--mode','launcher','--install-dir',str(root)])
    links=[];hashes={};retained=[]
    for folder in folders.values():
        path=folder/'Odessy.lnk';model.guard_path(path)
        if not path.exists():continue
        try:
            info=json.loads(powershell("$p=$env:ODESSY_SHORTCUT_JSON|ConvertFrom-Json; $s=(New-Object -ComObject WScript.Shell).CreateShortcut($p.path); @{target=$s.TargetPath;arguments=$s.Arguments;working=$s.WorkingDirectory}|ConvertTo-Json -Compress",payload=dict(path=str(path))))
            if Path(info['target'])!=target or info['arguments']!=arguments or Path(info['working'])!=target.parent:
                retained.append(str(path));continue
        except (OSError,ValueError,KeyError):retained.append(str(path));continue
        links.append(str(path));hashes[str(path)]=digest(path)
    return dict(schema_version=1,install_dir=str(root),executable=str(target),sha256=digest(executable),links=links,link_sha256=hashes),retained


def plan(root,bundle,executable,*,probe=running_processes,folders=None,validate_base=True):
    for path in (root,bundle,executable):model.guard_path(path)
    root=root.resolve();bundle=bundle.resolve();executable=executable.resolve()
    if removal_started(root):raise ValueError('已开始卸载的安装不执行升级，请选择新目录安装。')
    require_idle(root)
    if root in Path(sys.executable).resolve().parents or root in executable.parents:
        raise ValueError('请从安装目录外的新版 OdessySetup.exe 执行维护。')
    if (root/'.git').exists() or (root/'pyproject.toml').exists():raise ValueError('不能维护源码目录。')
    state=read_state(root)
    if state['status']!='completed' or Path(state['plan']['install_dir']).resolve()!=root:
        raise ValueError('维护需要原位置的完整安装记录。')
    if probe(root):raise ValueError('请先关闭此安装的终端、启动面板和模型服务。')
    if executable.suffix.lower()!='.exe':raise ValueError('请选择新版独立 EXE。')
    with executable.open('rb') as stream:
        if stream.read(2)!=b'MZ':raise ValueError('维护入口不是 Windows EXE。')
    new=workflow.build_plan(root,Path(state['plan']['cache_dir']),bundle,state['plan']['endpoint'],state['plan']['timeout'])
    if new['model']!=state['plan']['model']:raise ValueError('此版本涉及模型迁移，本轮仅支持相同模型的应用升级。')
    if validate_base:
        for step in workflow.steps_for(new):
            if step.name in {'runtime','dependencies','ollama_runtime','model','configuration'}:
                if step.validate() is None:raise ValueError('基础组件缺失，需后续组件修复：'+step.name)
    _,tool=application.validate_bundle(bundle)
    source=application.source_snapshot(application.REPO_ROOT)
    required=2*(sum(map(len,source.values()))+tool['unpacked_bytes']+executable.stat().st_size)+RESERVE_BYTES
    if shutil.disk_usage(root).free<required:raise ValueError('没有足够空间准备新版和保留回退文件。')
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
        with ollama_runtime.managed_service(root/'ollama-runtime',root/'models',state_dir,plan['endpoint'],plan['model'],popen=popen):
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
    if not isinstance(names,list) or not all(isinstance(name,str) for name in names) or len(names)>12 or len(set(names))!=len(names):raise ValueError('维护组件记录无效。')
    for name in names:
        if name in {'application','launcher','runtime','dependencies','ollama-runtime'}:continue
        if not isinstance(name,str) or not re.fullmatch(r'models/(?:blobs/sha256-[0-9a-f]{64}|manifests/registry\.ollama\.ai/library/[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*)',name):
            raise ValueError('维护组件路径越界。')
    return names


def _restore(root,data):
    folder=root/'.installer/updates'/data['id'];model.guard_path(folder)
    if data['status']=='preparing':
        data['status']='rolled_back';_save(root,data);return data
    if data['status']=='completed' and fingerprint(read_state(root))!=data.get('new_state_sha256'):
        raise ValueError('安装记录在升级后发生变化，已停止回退并保留文件。')
    # Check all original copies before changing either component.
    for name in _components(data):
        target=root/name;backup=folder/('backup-'+name)
        original=backup if backup.exists() else target
        if data['had_'+name] and (not original.exists() or _signature(root,original)!=data['old_signatures'][name]):
            raise ValueError('原组件或回退备份已变化，不能自动恢复：'+name)
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
        elif data['had_'+name] and not target.exists():raise ValueError('原组件和备份均缺失，不能自动恢复：'+name)
    save_state(root,copy.deepcopy(data['old_state']))
    data['status']='rolled_back';_save(root,data);return data


def recover(root,*,probe=running_processes):
    model.guard_path(root);root=root.resolve()
    if (root/'.git').exists() or (root/'pyproject.toml').exists():raise ValueError('不能回退源码目录。')
    if removal_started(root):raise ValueError('卸载中的安装不能回退。')
    if root in Path(sys.executable).resolve().parents:raise ValueError('请从安装目录外执行回退。')
    if probe(root):raise ValueError('请先关闭此安装的全部窗口和服务。')
    with writer_lock(root):
        data=journal(root)
        if not data or data['status']=='rolled_back':raise ValueError('没有可回退的维护记录。')
        if data.get('rollback_discarded'):raise ValueError('此次维护的回退备份已确认清理，不能恢复原版本。')
        try:return _restore(root,data)
        except BaseException as error:
            data.update(status='recovery_required',error=str(error));_save(root,data);raise


def execute(root,bundle,executable,expected,*,probe=running_processes,folders=None,
            planner=plan,assemble=application.assemble,verify=verify_staging,final_check=connection.inspect_application,repair=None):
    preview=planner(root,bundle,executable,probe=probe,folders=folders)
    if preview['plan_sha256']!=expected:raise ValueError('文件或版本已变化，请重新检查维护方案。')
    root=Path(preview['install_dir'])
    with writer_lock(root):
        preview=planner(root,bundle,executable,probe=probe,folders=folders)
        if preview['plan_sha256']!=expected:raise ValueError('文件或版本已变化，请重新检查维护方案。')
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
            if digest(launch/'OdessyLauncher.exe')!=preview['executable_sha256']:raise ValueError('复制时新版 EXE 发生变化。')
            (launch/'odessy-shortcuts.json').write_text(json.dumps(preview['launcher'],indent=2),encoding='utf-8')
            proof=verify(root,folder/'new-application',folder/'service',preview['new_plan'])
            if not proof.get('inference_verified') or not proof.get('tool_round_trip_verified'):raise ValueError('新版真实模型与工具检查未通过，原版本保留。')
            now=[]
            for name in ('application','launcher'):now.extend(_tree(root,root/name))
            if now!=preview['inventory']:raise ValueError('准备新版期间原应用文件发生变化，请重新检查。')
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
            if args.tool_bundle is None or args.executable is None:raise ValueError('请指定 --tool-bundle 和 --executable。')
            if args.apply:
                if not args.confirm:raise ValueError('请先检查并提供 --confirm 方案指纹。')
                result=execute(args.install_dir,args.tool_bundle,args.executable,args.confirm)
            else:result=plan(args.install_dir,args.tool_bundle,args.executable)
        print(json.dumps({key:value for key,value in result.items() if key not in {'inventory','old_state'}},indent=2,ensure_ascii=False));return 0
    except (OSError,ValueError,KeyError,DownloadError,subprocess.SubprocessError) as error:
        print('维护未完成：',error);return 1


if __name__=='__main__':raise SystemExit(main())
