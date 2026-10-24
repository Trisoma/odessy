"""Current-user Windows integration with conservative ownership cleanup."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from .maintenance import guard, digest, ManagedInstallation, read_json

DISPLAY_VERSION='0.5.0.dev0'
_IDENTITY=re.compile('[a-f0-9]{32}')


def _identity(record):
    identity=record.get('id')
    if not isinstance(identity,str) or not _IDENTITY.fullmatch(identity):
        raise ValueError('invalid installation identity')
    return identity


def _active_desktop(root,record):
    version=record.get('active')
    if not isinstance(version,str) or not _IDENTITY.fullmatch(version):
        raise ValueError('the installation has no active version')
    desktop=guard(root/'versions'/version/'desktop/odessy_desktop.exe')
    if not desktop.is_file():raise ValueError('installed desktop is unavailable')
    return version,desktop


def launch(root, mode='app', launch_id=None):
    if mode not in ('app','manage','uninstall'): raise ValueError('invalid launch mode')
    root=guard(root)
    record=ManagedInstallation(root).record()
    version,executable=_active_desktop(root,record)
    args=[str(executable)]
    if mode in ('manage','uninstall'):
        args+=['--'+mode,'--install-root',str(root)]
        helper=(record.get('windows_registration') or {}).get('helper')
        if isinstance(helper,str) and helper:args+=['--payload',helper]
    else:
        args+=['--installed','--install-root',str(root)]
    if launch_id is not None:
        from . import launch_receipts
        launch_receipts.prepare(root, launch_id)
        args += ['--launch-id', launch_id]
    # The long-lived desktop must not inherit the stdio bridge's response pipes.
    # Otherwise its helper exits but Flutter waits for EOF until the app closes.
    return subprocess.Popen(args,cwd=tempfile.gettempdir(),stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                            close_fds=True)


# Parameters are carried by environment, never interpolated into PowerShell code.
_SHORTCUT="$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:ODESSY_SHORTCUT);$s.TargetPath=$env:ODESSY_POWERSHELL;$s.Arguments=$env:ODESSY_ARGUMENTS;$s.WorkingDirectory=$env:TEMP;$s.Description='Odessy';$s.Save()"
_LAUNCHER='''param([switch]$Manage, [switch]$Uninstall)
$ErrorActionPreference='Stop'
$r=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'installation.json') -Raw | ConvertFrom-Json
if ($r.active -notmatch '^[a-f0-9]{32}$') { throw 'Invalid active version' }
$exe=Join-Path $PSScriptRoot ('versions/'+$r.active+'/desktop/odessy_desktop.exe')
Set-Location -LiteralPath $env:TEMP
if ($Uninstall) { & $exe --uninstall --install-root $PSScriptRoot } elseif ($Manage) { & $exe --manage --install-root $PSScriptRoot } else { & $exe --installed --install-root $PSScriptRoot }
'''


def _powershell():
    return str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')


def _registry_owner(key):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key) as handle:
            return winreg.QueryValueEx(handle,'InstallLocation')[0]
    except FileNotFoundError:
        return None


def _write_registry(key,values):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,key) as handle:
        for name,value in values.items():
            winreg.SetValueEx(handle,name,0,winreg.REG_DWORD if isinstance(value,int) else winreg.REG_SZ,value)


def _registry_values(key):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key) as handle:
            return {name:value for name,value,_ in
                    (winreg.EnumValue(handle,index) for index in range(winreg.QueryInfoKey(handle)[1]))}
    except FileNotFoundError:
        return None


def _delete_registry(key):
    import winreg
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER,key)
    except FileNotFoundError:
        pass


def _shortcut_path(identity):
    return guard(Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs'/('Odessy-'+identity+'.lnk'))


def _cache_helper(root,record,payload):
    """Persist a verified maintenance payload outside the installation."""
    from .package_context import context
    payload=guard(payload)
    context(payload=payload,root=root)
    manifest=read_json(payload/'setup-context.json')
    key_hash=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:32]
    helper=guard(Path(os.environ['LOCALAPPDATA'])/'OdessyMaintenance'/record['id']/key_hash)
    helper.mkdir(parents=True,exist_ok=True)
    for name,sha in manifest['files'].items():
        target=guard(helper/name)
        if target.exists():
            if digest(target)!=sha:raise ValueError('maintenance helper changed')
            continue
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(guard(payload/name),target)
        if digest(target)!=sha:raise ValueError('maintenance helper copy failed verification')
    (helper/'setup-context.json').write_text(json.dumps(manifest),encoding='utf-8')
    context(payload=helper,root=root)
    return helper


def _write_shortcut(shortcut,target,arguments):
    powershell=_powershell()
    env=dict(os.environ,ODESSY_SHORTCUT=str(shortcut),ODESSY_POWERSHELL=target,ODESSY_ARGUMENTS=arguments)
    subprocess.run([powershell,'-NoProfile','-NonInteractive','-Command',_SHORTCUT],env=env,check=True,
                   stdin=subprocess.DEVNULL,timeout=30,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))


def register(root, record, payload=None):
    """Create the Start-menu entry, maintenance cache and Installed-apps record.

    Identity and payload ownership are validated before anything is written. A
    failed registry write leaves a recorded partial state and a working shortcut,
    so the installation stays reachable instead of becoming orphaned.
    """
    if os.name!='nt': return {}
    root=guard(root)
    if record.get('root')!=str(root): raise ValueError('registration root mismatch')
    identity=_identity(record)
    version,desktop=_active_desktop(root,record)
    previous=record.get('windows_registration') or {}
    if previous and previous.get('installation_id') not in (None,identity):
        raise ValueError('registration belongs to another installation')
    helper=previous.get('helper')
    helper_files=previous.get('helper_files')
    if payload is not None:
        helper=str(_cache_helper(root,record,payload))
        helper_files=dict(read_json(Path(helper)/'setup-context.json')['files'])
        helper_files['setup-context.json']=digest(Path(helper)/'setup-context.json')
    if helper:helper=guard(Path(helper))
    launcher=guard(root/'launch.ps1')
    if launcher.exists() and digest(launcher)!=previous.get('files',{}).get(str(launcher)):
        raise ValueError('launcher is externally owned or modified')
    shortcut=_shortcut_path(identity)
    if shortcut.exists() and digest(shortcut)!=previous.get('files',{}).get(str(shortcut)):
        raise ValueError('shortcut is externally owned or modified')
    key=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\Odessy-'+identity
    owner=_registry_owner(key)
    if owner is not None and owner!=str(root): raise ValueError('registration is externally owned')
    launcher.write_text(_LAUNCHER,encoding='utf-8')
    if helper and not (helper/'desktop/odessy_desktop.exe').is_file():
        raise ValueError('maintenance payload lacks the desktop application')
    shortcut.parent.mkdir(parents=True,exist_ok=True)
    # Launch the active owned version, never a stale cache copy.
    _write_shortcut(shortcut,str(desktop),subprocess.list2cmdline(['--installed','--install-root',str(root)]))
    if helper:
        manage_command=subprocess.list2cmdline([str(helper/'desktop/odessy_desktop.exe'),'--manage',
                                                '--payload',str(helper),'--install-root',str(root)])
        uninstall_command=subprocess.list2cmdline([str(helper/'desktop/odessy_desktop.exe'),'--uninstall',
                                                '--payload',str(helper),'--install-root',str(root)])
    else:
        powershell=_powershell()
        manage_command=subprocess.list2cmdline([powershell,'-NoProfile','-NonInteractive','-File',str(launcher),'-Manage'])
        uninstall_command=subprocess.list2cmdline([powershell,'-NoProfile','-NonInteractive','-File',str(launcher),'-Uninstall'])
    values={'DisplayName':'Odessy','DisplayVersion':DISPLAY_VERSION,'Publisher':'Trisoma',
            'InstallLocation':str(root),'UninstallString':uninstall_command,'ModifyPath':manage_command,
            'NoModify':0,'NoRepair':1,'DisplayIcon':str(desktop)}
    resources={'schema_version':1,'installation_id':identity,'version':version,'files':{str(launcher):digest(launcher),
               str(shortcut):digest(shortcut)},'shortcut':str(shortcut),'shortcut_target':str(desktop),
               'registry_key':key,'registry_values':values,'helper':str(helper) if helper else None,'state':'partial'}
    record['windows_registration']=resources
    if helper_files is not None:resources['helper_files']=helper_files
    try:
        _write_registry(key,values)
    except OSError as error:
        resources['error']='Installed-apps registration was not written.'
        raise RuntimeError('Windows registration is incomplete; retry it from Odessy management') from error
    resources['state']='complete'
    return resources


def refresh(root, record):
    """Keep the shortcut pointing at the version that is actually active."""
    if os.name!='nt': return record.get('windows_registration') or {}
    resources=record.get('windows_registration') or {}
    if resources.get('state')!='complete' or not resources.get('shortcut'):
        return resources
    root=guard(root)
    _,desktop=_active_desktop(root,record)
    shortcut=guard(Path(resources['shortcut']))
    if shortcut.exists() and digest(shortcut)!=resources.get('files',{}).get(str(shortcut)):
        raise ValueError('shortcut was modified externally; re-register from management')
    _write_shortcut(shortcut,str(desktop),subprocess.list2cmdline(['--installed','--install-root',str(root)]))
    resources.setdefault('files',{})[str(shortcut)]=digest(shortcut)
    resources['shortcut_target']=str(desktop)
    record['windows_registration']=resources
    return resources


def unregister(root, record):
    if os.name!='nt': return []
    root=guard(root)
    resources=record.get('windows_registration',{})
    identity=_identity(record)
    retained=[]
    # Only exact known paths can be removed, even if the ownership record is edited.
    allowed={str(root/'launch.ps1'),str(_shortcut_path(identity))}
    for name,sha in resources.get('files',{}).items():
        if name not in allowed: raise ValueError('invalid registration ownership path')
        path=guard(name)
        if path.is_file() and digest(path)==sha: path.unlink()
        elif path.exists(): retained.append(name)
    key=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\Odessy-'+identity
    if resources.get('registry_key')==key:
        actual=_registry_values(key)
        if actual is None: pass
        elif actual==resources.get('registry_values'): _delete_registry(key)
        else: retained.append(key)
    return retained


def helper_cleanup(record):
    """Remove only recorded, unchanged cache files; never schedule self-deletion."""
    if os.name!='nt': return {'state':'unsupported'}
    resources=record.get('windows_registration') or {}
    helper=resources.get('helper')
    if not isinstance(helper,str) or not helper:return {'state':'none'}
    root=Path(os.environ['LOCALAPPDATA'])/'OdessyMaintenance'
    try:
        path=guard(Path(helper))
        identity=_identity(record)
        if path.parent!=guard(root/identity) or not _IDENTITY.fullmatch(path.name):
            return {'state':'retained','reason':'cache does not belong to this installation'}
    except ValueError:
        return {'state':'retained','reason':'unsafe cache path'}
    if not path.is_dir():return {'state':'absent'}
    current=Path(sys.executable).resolve()
    if current.is_relative_to(path.resolve()):
        return {'state':'retained','reason':'maintenance helper is running; no deletion scheduled'}
    inventory=resources.get('helper_files')
    if not isinstance(inventory,dict) or not inventory or len(inventory)>100001:
        return {'state':'retained','reason':'cache has no recorded ownership inventory'}
    files=[];directories={path}
    try:
        for name,sha in inventory.items():
            if (not isinstance(name,str) or not name or '\\' in name or ':' in name
                    or name.startswith('/') or '..' in Path(name).parts
                    or not re.fullmatch('[a-f0-9]{64}',str(sha))):
                raise ValueError('invalid cache ownership inventory')
            target=guard(path/name)
            if not target.is_relative_to(path) or target==path:raise ValueError('invalid cache target')
            files.append((target,sha))
            directories.update(parent for parent in target.parents if parent.is_relative_to(path))
    except (OSError,ValueError):
        return {'state':'retained','reason':'unsafe cache ownership inventory'}
    removed=[];retained=[]
    for target,sha in files:
        try:
            guard(target)
            if target.is_file() and digest(target)==sha:
                target.unlink();removed.append(str(target.relative_to(path)))
            elif target.exists():retained.append(str(target.relative_to(path)))
        except (OSError,ValueError):retained.append(str(target.relative_to(path)))
    for directory in sorted(directories,key=lambda p:len(p.parts),reverse=True):
        try:guard(directory).rmdir()
        except (OSError,ValueError):pass  # Nonempty directories contain retained/unowned files.
    return {'state':'retained' if path.exists() else 'removed','removed':removed,
            'retained':retained,'reason':'only unchanged recorded files are removed; other files stay'}
