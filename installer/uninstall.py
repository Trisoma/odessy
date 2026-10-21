"""Preview-bound removal of one dedicated installation; retain data by default."""
from __future__ import annotations
import argparse
import json
import os
import stat
import sys
import uuid
from pathlib import Path
from installer.download import DownloadError
from installer.checkpoint import fingerprint, read_state, writer_lock
from installer.model import guard_path
from installer.manifest import _unique_keys
from installer.atomic import replace as replace_record
from installer import pathing
from installer.shortcuts import digest, powershell, user_folders

RECORD = '.installer/uninstall.json'
COMPONENTS = {'application':'odessy-application.json','dependencies':'odessy-dependencies.json',
              'runtime':'odessy-runtime.json','ollama-runtime':'odessy-ollama-runtime.json',
              'launcher':'odessy-shortcuts.json','ollama-state':None}


def removal_started(root):
    guard_path(root/RECORD)
    return (root/RECORD).exists()


def running_processes(root):
    if os.name!='nt': return []
    data=powershell("$p=$env:ODESSY_SHORTCUT_JSON|ConvertFrom-Json; "
        "@(Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -and $_.ExecutablePath.StartsWith($p.root+'\\',[StringComparison]::OrdinalIgnoreCase)} | "
        "Select-Object ProcessId,ExecutablePath) | ConvertTo-Json -Compress",payload=dict(root=str(root)))
    values=json.loads(data) if data else []
    return values if isinstance(values,list) else [values]


def _json(path):
    guard_path(path)
    if path.stat().st_size>4*1024*1024: raise ValueError('安装标记过大，已保留文件。')
    return json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=_unique_keys)


def _entry(root,path):
    guard_path(path)
    # guard_path already rejects links/reparse points; comparing Path.resolve()
    # with Path would reject every 8.3 short installation path instead.
    if root not in path.parents or not pathing.link_free(path): raise ValueError('清理路径超出安装目录。')
    info=path.stat()
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError('安装目录含非普通文件，已停止预览：'+str(path))
    return dict(path=path.relative_to(root).as_posix(),directory=stat.S_ISDIR(info.st_mode),
                size=0 if stat.S_ISDIR(info.st_mode) else info.st_size,mtime=info.st_mtime_ns)


def _tree(root,path):
    if not path.exists():
        guard_path(path); return []
    entry=_entry(root,path); result=[entry]
    if entry['directory']:
        for child in sorted(path.iterdir()): result.extend(_tree(root,child))
    return result


def plan(root, *, keep_models=True, keep_config=True, folders=None, probe=running_processes):
    guard_path(root);root=root.resolve()
    from installer.maintenance import require_idle
    require_idle(root)
    for name in ('SystemRoot','WINDIR','ProgramFiles','ProgramFiles(x86)'):
        if os.environ.get(name):
            protected=Path(os.environ[name]).resolve()
            if root==protected or protected in root.parents: raise ValueError('拒绝清理系统目录。')
    # A source checkout is never an uninstall target, even if a record was copied in.
    if (root/'.git').exists() or (root/'pyproject.toml').exists(): raise ValueError('源码目录不能作为卸载目标。')
    state=read_state(root)
    if not pathing.same(state['plan'].get('install_dir',''),root): raise ValueError('安装目录已移动，拒绝清理。')
    if state['status']!='completed' or not state['summary']['workflow_complete']:
        raise ValueError('本模块仅支持有完整记录的安装；未完成安装先保留。')
    previous=_json(root/RECORD) if removal_started(root) else None
    if previous is not None and (not isinstance(previous,dict) or previous.get('schema_version')!=1 or previous.get('status') not in {'running','failed','completed'} or previous.get('install_dir')!=str(root) or previous.get('state_sha256')!=fingerprint(state)):
        raise ValueError('卸载记录与原安装不一致，已保留。')
    executable=Path(sys.executable).resolve()
    if executable==root or root in executable.parents:
        raise ValueError('请从安装目录外的 OdessySetup.exe 执行卸载，再关闭已安装的启动面板。')
    processes=probe(root)
    if processes: raise ValueError('请先关闭 Odessy 终端、启动面板及此安装的模型服务，再预览卸载。')
    inventory=[];selected=[]
    for name,marker in COMPONENTS.items():
        folder=root/name;guard_path(folder)
        if not folder.exists():continue
        if not folder.is_dir(): raise ValueError('组件路径不是目录：'+str(folder))
        if marker and previous is None:
            data=_json(folder/marker)
            if not isinstance(data,dict) or data.get('schema_version')!=1: raise ValueError('组件缺少有效安装标记：'+name)
        selected.append(name);inventory.extend(_tree(root,folder))
    if not keep_models:
        selected.append('models');inventory.extend(_tree(root,root/'models'))
    if not keep_config:
        selected.append('config/odessy.env');inventory.extend(_tree(root,root/'config/odessy.env'))
    links=[];retained=[]
    marker=root/'launcher/odessy-shortcuts.json'
    shortcut_record=_json(marker) if marker.exists() else (previous or {}).get('shortcut_record')
    if shortcut_record:
        data=shortcut_record;folders=user_folders() if folders is None else folders
        allowed={folder/'Odessy.lnk' for folder in folders.values()}
        if data.get('install_dir')!=str(root) or data.get('executable')!=str(root/'launcher/OdessyLauncher.exe'):
            raise ValueError('快捷方式记录不属于此安装。')
        for name,expected in data.get('link_sha256',{}).items():
            path=Path(name)
            if path not in allowed: raise ValueError('快捷方式路径不属于当前用户的指定位置。')
            guard_path(path)
            if path.exists():
                if path.is_file() and digest(path)==expected:links.append(dict(path=str(path),sha256=expected))
                else:retained.append(str(path))
    info=dict(schema_version=1,install_dir=str(root),state_sha256=fingerprint(state),
              keep_models=keep_models,keep_config=keep_config,components=selected,
              inventory=inventory,shortcuts=links,retained_shortcuts=retained,shortcut_record=shortcut_record,
              bytes_to_remove=sum(item['size'] for item in inventory),
              retained_root_entries=[p.name for p in sorted(root.iterdir()) if p.name not in COMPONENTS and p.name not in {'models','config'}],
              shared_cache_retained=state['plan'].get('cache_dir'))
    info['plan_sha256']=fingerprint(info)
    return info


def _save(root,value):
    target=root/RECORD;guard_path(target)
    temporary=target.with_name('.uninstall-'+uuid.uuid4().hex+'.tmp')
    guard_path(temporary)
    with temporary.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2);stream.flush();os.fsync(stream.fileno())
    try:replace_record(temporary,target)
    finally:temporary.unlink(missing_ok=True)


def execute(root, expected, *, keep_models=True,keep_config=True,folders=None,probe=running_processes):
    preview=plan(root,keep_models=keep_models,keep_config=keep_config,folders=folders,probe=probe)
    if preview['plan_sha256']!=expected: raise ValueError('文件或选项已变化，请重新预览后确认。')
    root=Path(preview['install_dir'])
    with writer_lock(root):
        current=plan(root,keep_models=keep_models,keep_config=keep_config,folders=folders,probe=probe)
        if current['plan_sha256']!=expected: raise ValueError('文件或选项已变化，请重新预览后确认。')
        result=dict(schema_version=1,install_dir=str(root),state_sha256=current['state_sha256'],
                    status='running',keep_models=keep_models,keep_config=keep_config,shortcut_record=current['shortcut_record'],
                    removed_files=0,removed_shortcuts=0,error=None)
        # From this point launching/resuming is blocked even after a partial failure.
        _save(root,result)
        try:
            for link in current['shortcuts']:
                path=Path(link['path']);guard_path(path)
                if digest(path)!=link['sha256']: raise ValueError('快捷方式发生变化，请重新预览。')
                path.unlink();result['removed_shortcuts']+=1
            files=[item for item in current['inventory'] if not item['directory']]
            directories=[item for item in current['inventory'] if item['directory']]
            for item in files:
                path=root/item['path']
                if _entry(root,path)!=item: raise ValueError('清理时文件发生变化，已停止：'+str(path))
                path.unlink();result['removed_files']+=1
            for item in sorted(directories,key=lambda item:len(Path(item['path']).parts),reverse=True):
                path=root/item['path'];guard_path(path)
                if root not in path.parents or not pathing.link_free(path): raise ValueError('目录超出卸载范围。')
                path.rmdir()  # New/unlisted files cause a stop; never recursively erase them.
            result['status']='completed';_save(root,result)
        except BaseException as error:
            result.update(status='failed',error=str(error));_save(root,result);raise
    return result


def summary(preview):
    return {key:value for key,value in preview.items() if key!='inventory'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir',type=Path,required=True)
    parser.add_argument('--delete-models',action='store_true');parser.add_argument('--delete-config',action='store_true')
    parser.add_argument('--remove',action='store_true');parser.add_argument('--confirm',help='Exact plan_sha256 from a fresh preview.')
    args=parser.parse_args(argv)
    try:
        options=dict(keep_models=not args.delete_models,keep_config=not args.delete_config)
        if args.remove:
            if not args.confirm:raise ValueError('请先预览，并用 --confirm 指定该预览的 plan_sha256。')
            result=execute(args.install_dir,args.confirm,**options)
        else:result=summary(plan(args.install_dir,**options))
        print(json.dumps(result,indent=2,ensure_ascii=False));return 0
    except (OSError,ValueError,KeyError,TypeError,DownloadError) as error:
        print('卸载未完成：',error);return 1


if __name__=='__main__':raise SystemExit(main())
