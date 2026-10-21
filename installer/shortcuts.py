"""Current-user shortcuts backed by a private, persistent standalone launcher."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from installer.checkpoint import read_state, writer_lock
from installer.model import guard_path
from installer.process import owned_directory

NAME = 'Odessy.lnk'
MARKER = 'odessy-shortcuts.json'


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): value.update(block)
    return value.hexdigest()


def powershell(script, *, payload=None):
    if os.name != 'nt': raise ValueError('Shortcuts require Windows.')
    env={k:v for k,v in os.environ.items() if k.upper()!='PSMODULEPATH'}
    if payload is not None: env['ODESSY_SHORTCUT_JSON']=json.dumps(payload,ensure_ascii=True)
    executable=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(executable),'-NoProfile','-NonInteractive','-Command',
        "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "+script],
        env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode: raise ValueError('无法创建快捷方式：'+(result.stderr or result.stdout)[-1000:])
    return result.stdout.strip()


def user_folders():
    data=json.loads(powershell("@{desktop=[Environment]::GetFolderPath('DesktopDirectory'); programs=[Environment]::GetFolderPath('Programs')} | ConvertTo-Json -Compress"))
    if not data.get('desktop') or not data.get('programs'): raise ValueError('无法定位当前账户的桌面或开始菜单。')
    return {key:Path(value) for key,value in data.items()}


def write_link(path, target, root):
    # User paths are data, never interpolated into PowerShell source. WSH output
    # is read back and verified before publishing a shortcut with exclusive create.
    with tempfile.TemporaryDirectory(prefix='odessy-shortcut-') as folder:
        staging=Path(folder)/NAME
        payload=dict(path=str(staging),target=str(target),arguments=subprocess.list2cmdline(['--mode','launcher','--install-dir',str(root)]),working=str(target.parent))
        powershell("$p=$env:ODESSY_SHORTCUT_JSON|ConvertFrom-Json; $shell=New-Object -ComObject WScript.Shell; "
            "$link=$shell.CreateShortcut($p.path); $link.TargetPath=$p.target; $link.Arguments=$p.arguments; "
            "$link.WorkingDirectory=$p.working; $link.Description='Start Odessy'; $link.IconLocation=$p.target+',0'; $link.Save(); "
            "$saved=$shell.CreateShortcut($p.path); if ($saved.TargetPath -ne $p.target -or $saved.Arguments -ne $p.arguments -or $saved.WorkingDirectory -ne $p.working) {throw 'Shortcut verification failed'}",payload=payload)
        data=staging.read_bytes()
        if not data: raise ValueError('快捷方式内容为空。')
        stream=path.open('xb')
        try:
            with stream: stream.write(data)
        except BaseException:
            path.unlink(missing_ok=True)
            raise


def plan(root, executable, *, desktop=True, start_menu=True, folders=None):
    if not desktop and not start_menu: raise ValueError('请至少选择一个快捷方式位置。')
    for path in (root,executable):
        if not path.is_absolute(): raise ValueError('请使用绝对路径。')
        guard_path(path)
    root=root.resolve(); executable=executable.resolve()
    from installer.maintenance import require_idle
    require_idle(root)
    from installer.uninstall import removal_started
    if removal_started(root): raise ValueError('此安装已执行卸载，不能创建启动入口。')
    state=read_state(root)
    if state['status']!='completed' or not state['summary']['workflow_complete'] or Path(state['plan']['install_dir']).resolve()!=root:
        raise ValueError('请先完成原位置的应用安装。')
    if executable.suffix.lower()!='.exe': raise ValueError('请选择已构建的独立安装器 EXE。')
    with executable.open('rb') as stream:
        if stream.read(2)!=b'MZ': raise ValueError('启动源不是 Windows EXE。')
    folders=user_folders() if folders is None else folders
    links=[]
    for enabled,key in ((desktop,'desktop'),(start_menu,'programs')):
        if enabled:
            folder=folders[key]
            if not folder.is_absolute() or not folder.is_dir(): raise ValueError('快捷方式目录不存在：'+str(folder))
            guard_path(folder); links.append(folder/NAME)
    launcher=root/'launcher'; guard_path(launcher)
    guard_path(launcher/'OdessyLauncher.exe')
    result=dict(schema_version=1,install_dir=str(root),executable=str(launcher/'OdessyLauncher.exe'),
                sha256=digest(executable),links=[str(path) for path in links])
    result['managed']=launcher.exists();result['pending_links']=[]
    if result['managed']:
        marker=launcher/MARKER;guard_path(marker)
        existing=json.loads(marker.read_text(encoding='utf-8'))
        if (not isinstance(existing,dict) or any(existing.get(key)!=result[key] for key in ('schema_version','install_dir','executable','sha256'))
            or digest(Path(result['executable']))!=result['sha256']):
            raise ValueError('已有启动入口版本不同或损坏，已保留；请先修复升级启动入口。')
        allowed={folder/NAME for folder in folders.values()}
        old_links=[Path(name) for name in existing.get('links',[])]
        if any(path not in allowed for path in old_links) or any(Path(name) not in allowed for name in existing.get('link_sha256',{})):
            raise ValueError('快捷方式记录路径不属于当前用户。')
        requested=list(links);links=list(dict.fromkeys([*old_links,*requested]));result['links']=[str(path) for path in links]
        for path in links:
            guard_path(path)
            if path.exists():
                if not path.is_file() or digest(path)!=existing.get('link_sha256',{}).get(str(path)):
                    raise ValueError('已有快捷方式已变化或属于其他安装，已保留。')
            elif path in requested:result['pending_links'].append(str(path))
        result['existing']=not result['pending_links'];result['record_sha256']=digest(marker)
    else:
        if shutil.disk_usage(root).free < executable.stat().st_size+16*1024**2:
            raise ValueError('磁盘空间不足，无法保存独立启动程序；应用安装已保留。')
        for path in links:
            guard_path(path)
            if path.exists():raise ValueError('已有同名快捷方式，已保留：'+str(path))
        result['existing']=False;result['pending_links']=result['links']
    return result


def create(root, executable, *, desktop=True, start_menu=True, folders=None, writer=write_link):
    plan(root,executable,desktop=desktop,start_menu=start_menu,folders=folders)
    with writer_lock(root):
        preview=plan(root,executable,desktop=desktop,start_menu=start_menu,folders=folders)
        if preview['existing']:return preview
        target=Path(preview['executable']);created={}
        record={key:preview[key] for key in ('schema_version','install_dir','executable','sha256','links')}
        def publish():
            for name in preview['pending_links']:
                path=Path(name);writer(path,target,root);created[name]=digest(path)
            record['link_sha256']={}
            marker=target.parent/MARKER
            if preview['managed']:
                if digest(marker)!=preview['record_sha256']:raise ValueError('快捷方式记录发生变化，请重试。')
                record['link_sha256'].update(json.loads(marker.read_text(encoding='utf-8'))['link_sha256'])
            record['link_sha256'].update(created)
            temporary=marker.with_name('.shortcuts-'+uuid.uuid4().hex+'.tmp');guard_path(temporary)
            try:
                with temporary.open('x',encoding='utf-8') as stream:
                    json.dump(record,stream,indent=2);stream.flush();os.fsync(stream.fileno())
                os.replace(temporary,marker)
            finally:temporary.unlink(missing_ok=True)
        try:
            if preview['managed']:publish()
            else:
                with owned_directory(target.parent):
                    with executable.open('rb') as source,target.open('xb') as output:shutil.copyfileobj(source,output,1024*1024)
                    if digest(target)!=preview['sha256']:raise ValueError('复制时 EXE 发生变化。')
                    publish()
            return dict(record,existing=False)
        except BaseException:
            for name,expected in created.items():
                path=Path(name)
                try:
                    guard_path(path)
                    if digest(path)==expected:path.unlink()
                except (OSError,ValueError):pass
            raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir',type=Path,required=True)
    parser.add_argument('--executable',type=Path,default=Path(sys.executable) if getattr(sys,'frozen',False) else None)
    parser.add_argument('--no-desktop',action='store_true'); parser.add_argument('--no-start-menu',action='store_true')
    parser.add_argument('--create',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.executable is None: raise ValueError('源码入口需用 --executable 指定独立 EXE。')
        action=create if args.create else plan
        report=action(args.install_dir,args.executable,desktop=not args.no_desktop,start_menu=not args.no_start_menu)
        print(json.dumps(report,indent=2,ensure_ascii=False))
        print('启动入口和快捷方式已准备。' if args.create else '仅预览；添加 --create 才会创建。')
        return 0
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print('快捷方式未完成：',error); return 1


if __name__=='__main__': raise SystemExit(main())
