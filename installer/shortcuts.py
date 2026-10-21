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
from installer import pathing, shelllink
from installer.atomic import replace as replace_record

NAME = 'Odessy.lnk'
MARKER = 'odessy-shortcuts.json'


def launcher_arguments(root):
    return subprocess.list2cmdline(['--mode','launcher','--install-dir',str(root)])


def matches_launcher_arguments(arguments, root):
    """Recognize our generated arguments, allowing equivalent path spellings.

    Require the exact generated command shape so extra flags or requests never
    make a modified shortcut eligible for adoption during maintenance.
    """
    prefix='--mode launcher --install-dir '
    if not isinstance(arguments,str) or not arguments.startswith(prefix):return False
    directory=arguments[len(prefix):]
    if directory.startswith('"') and directory.endswith('"'):directory=directory[1:-1]
    return (bool(directory) and Path(directory).is_absolute()
            and launcher_arguments(directory)==arguments and pathing.same(directory,root))


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): value.update(block)
    return value.hexdigest()


def powershell(script, *, payload=None):
    if os.name != 'nt': raise ValueError('Shortcuts require Windows.')
    env={k:v for k,v in os.environ.items() if k.upper()!='PSMODULEPATH'}
    # Use only Windows' built-in modules. Dropping PSModulePath entirely makes
    # cold runners rediscover unrelated installed modules and can time out.
    env['PSModulePath']=str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/Modules')
    if payload is not None: env['ODESSY_SHORTCUT_JSON']=json.dumps(payload,ensure_ascii=True)
    executable=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    # Callers may mutate files (e.g. create a junction); do not replay a script
    # after a timeout when its side effects could already have happened.
    result=subprocess.run([str(executable),'-NoProfile','-NonInteractive','-Command',
        "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "+script],
        env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode: raise ValueError('Cannot create shortcuts: '+(result.stderr or result.stdout)[-1000:])
    return result.stdout.strip()


def user_folders():
    data=json.loads(powershell("@{desktop=[Environment]::GetFolderPath('DesktopDirectory'); programs=[Environment]::GetFolderPath('Programs')} | ConvertTo-Json -Compress"))
    if not data.get('desktop') or not data.get('programs'): raise ValueError('Cannot locate the desktop or Start menu for the current account.')
    return {key:Path(value) for key,value in data.items()}


def write_link(path, target, root):
    # WScript converts target paths through the system ANSI code page. Use the
    # Unicode shell interface and verify the staged link before publishing it.
    with tempfile.TemporaryDirectory(prefix='odessy-shortcut-') as folder:
        staging=Path(folder)/NAME
        arguments=launcher_arguments(root)
        shelllink.write(staging,target,arguments,target.parent)
        saved=shelllink.read(staging)
        if (not pathing.same(saved['target'],target) or saved['arguments']!=arguments
            or not pathing.same(saved['working'],target.parent)):
            raise ValueError('Shortcut verification failed.')
        data=staging.read_bytes()
        if not data: raise ValueError('Shortcut content is empty.')
        stream=path.open('xb')
        try:
            with stream: stream.write(data)
        except BaseException:
            path.unlink(missing_ok=True)
            raise


def plan(root, executable, *, desktop=True, start_menu=True, folders=None):
    if not desktop and not start_menu: raise ValueError('Select at least one shortcut location.')
    for path in (root,executable):
        if not path.is_absolute(): raise ValueError('Use an absolute path.')
        guard_path(path)
    root=root.resolve(); executable=executable.resolve()
    from installer.maintenance import require_idle
    require_idle(root)
    from installer.uninstall import removal_started
    if removal_started(root): raise ValueError('This installation has been uninstalled; launcher entries cannot be created.')
    state=read_state(root)
    if state['status']!='completed' or not state['summary']['workflow_complete'] or Path(state['plan']['install_dir']).resolve()!=root:
        raise ValueError('Complete the application installation at the original location first.')
    if executable.suffix.lower()!='.exe': raise ValueError('Select a built standalone installer EXE.')
    with executable.open('rb') as stream:
        if stream.read(2)!=b'MZ': raise ValueError('Launch source is not a Windows EXE.')
    folders=user_folders() if folders is None else folders
    links=[]
    for enabled,key in ((desktop,'desktop'),(start_menu,'programs')):
        if enabled:
            folder=folders[key]
            if not folder.is_absolute() or not folder.is_dir(): raise ValueError('Shortcut directory does not exist: '+str(folder))
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
            raise ValueError('An existing launcher entry is a different version or corrupt and was preserved; repair the launcher entry first.')
        allowed={folder/NAME for folder in folders.values()}
        old_links=[Path(name) for name in existing.get('links',[])]
        if any(path not in allowed for path in old_links) or any(Path(name) not in allowed for name in existing.get('link_sha256',{})):
            raise ValueError('Shortcut record path does not belong to the current user.')
        requested=list(links);links=list(dict.fromkeys([*old_links,*requested]));result['links']=[str(path) for path in links]
        for path in links:
            guard_path(path)
            if path.exists():
                if not path.is_file() or digest(path)!=existing.get('link_sha256',{}).get(str(path)):
                    raise ValueError('An existing shortcut changed or belongs to another installation; preserved.')
            elif path in requested:result['pending_links'].append(str(path))
        result['existing']=not result['pending_links'];result['record_sha256']=digest(marker)
    else:
        if shutil.disk_usage(root).free < executable.stat().st_size+16*1024**2:
            raise ValueError('Not enough disk space to save the standalone launcher; the application installation was preserved.')
        for path in links:
            guard_path(path)
            if path.exists():raise ValueError('A shortcut with the same name already exists; preserved: '+str(path))
        result['existing']=False;result['pending_links']=result['links']
    return result


def create(root, executable, *, desktop=True, start_menu=True, folders=None, writer=write_link):
    preview=plan(root,executable,desktop=desktop,start_menu=start_menu,folders=folders)
    root=Path(preview['install_dir'])
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
                if digest(marker)!=preview['record_sha256']:raise ValueError('Shortcut record changed; try again.')
                record['link_sha256'].update(json.loads(marker.read_text(encoding='utf-8'))['link_sha256'])
            record['link_sha256'].update(created)
            temporary=marker.with_name('.shortcuts-'+uuid.uuid4().hex+'.tmp');guard_path(temporary)
            try:
                with temporary.open('x',encoding='utf-8') as stream:
                    json.dump(record,stream,indent=2);stream.flush();os.fsync(stream.fileno())
                replace_record(temporary,marker)
            finally:temporary.unlink(missing_ok=True)
        try:
            if preview['managed']:publish()
            else:
                with owned_directory(target.parent):
                    with executable.open('rb') as source,target.open('xb') as output:shutil.copyfileobj(source,output,1024*1024)
                    if digest(target)!=preview['sha256']:raise ValueError('The EXE changed during copying.')
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
        if args.executable is None: raise ValueError('The source entry requires --executable to point at the standalone EXE.')
        action=create if args.create else plan
        report=action(args.install_dir,args.executable,desktop=not args.no_desktop,start_menu=not args.no_start_menu)
        print(json.dumps(report,indent=2,ensure_ascii=False))
        print('Launcher entry and shortcuts are ready.' if args.create else 'Preview only; add --create to create them.')
        return 0
    except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
        print('Shortcuts did not complete: ',error); return 1


if __name__=='__main__': raise SystemExit(main())
