"""Current-user Windows integration with conservative ownership cleanup."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from .maintenance import guard, digest, ManagedInstallation


def launch(root, mode='app'):
    if mode not in ('app','manage'): raise ValueError('invalid launch mode')
    root=guard(root)
    record=ManagedInstallation(root).record()
    executable=guard(root/'versions'/record['active']/'desktop'/'odessy_desktop.exe')
    if not executable.is_file(): raise ValueError('installed desktop is unavailable')
    args=[str(executable)]
    if mode=='manage': args+=['--manage','--install-root',str(root)]
    return subprocess.Popen(args,cwd=tempfile.gettempdir(),stdin=subprocess.DEVNULL)


# Parameters are carried by environment, never interpolated into PowerShell code.
_SHORTCUT="$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:ODESSY_SHORTCUT);$s.TargetPath=$env:ODESSY_POWERSHELL;$s.Arguments=$env:ODESSY_ARGUMENTS;$s.WorkingDirectory=$env:TEMP;$s.Save()"
_LAUNCHER='''param([switch]$Manage)
$ErrorActionPreference='Stop'
$r=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'installation.json') -Raw | ConvertFrom-Json
if ($r.active -notmatch '^[a-f0-9]{32}$') { throw 'Invalid active version' }
$exe=Join-Path $PSScriptRoot ('versions/'+$r.active+'/desktop/odessy_desktop.exe')
Set-Location -LiteralPath $env:TEMP
if ($Manage) { & $exe --manage --install-root $PSScriptRoot } else { & $exe }
'''


def _powershell():
    return str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')


def register(root, record, payload=None):
    if os.name!='nt': return {}
    import winreg
    root=guard(root)
    if record.get('root')!=str(root): raise ValueError('registration root mismatch')
    identity=record['id']
    if not isinstance(identity,str) or len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity):raise ValueError('invalid installation identity')
    helper=None
    if payload is not None:
        import shutil
        from .package_context import context
        from .maintenance import read_json
        payload=guard(payload)
        context(payload=payload,root=root)
        manifest=read_json(payload/'setup-context.json')
        key_hash=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:32]
        helper=guard(Path(os.environ['LOCALAPPDATA'])/'OdessyMaintenance'/identity/key_hash)
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
    if len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity): raise ValueError('invalid installation identity')
    key=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\Odessy-'+identity
    previous=record.get('windows_registration',{})
    launcher=guard(root/'launch.ps1')
    if launcher.exists() and digest(launcher)!=previous.get('files',{}).get(str(launcher)):
        raise ValueError('launcher is externally owned or modified')
    shortcut=guard(Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs'/('Odessy-'+identity+'.lnk'))
    if shortcut.exists() and digest(shortcut)!=previous.get('files',{}).get(str(shortcut)):
        raise ValueError('shortcut is externally owned or modified')
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key) as handle:
            owner=winreg.QueryValueEx(handle,'InstallLocation')[0]
            if owner!=str(root): raise ValueError('registration is externally owned')
    except FileNotFoundError: pass
    launcher.write_text(_LAUNCHER,encoding='utf-8')
    powershell=_powershell()
    arguments=subprocess.list2cmdline(['-NoProfile','-NonInteractive','-File',str(launcher)])
    shortcut.parent.mkdir(parents=True,exist_ok=True)
    shortcut_target=str(helper/'desktop/odessy_desktop.exe') if helper else str(root/'versions'/record['active']/'desktop/odessy_desktop.exe')
    shortcut_arguments=subprocess.list2cmdline(['--installed','--install-root',str(root)]+(['--payload',str(helper)] if helper else []))
    env=dict(os.environ,ODESSY_SHORTCUT=str(shortcut),ODESSY_POWERSHELL=shortcut_target,ODESSY_ARGUMENTS=shortcut_arguments)
    subprocess.run([powershell,'-NoProfile','-NonInteractive','-Command',_SHORTCUT],env=env,check=True,stdin=subprocess.DEVNULL,timeout=30)
    manage_command=subprocess.list2cmdline([str(helper/'desktop/odessy_desktop.exe'),'--manage','--payload',str(helper),'--install-root',str(root)]) if helper else subprocess.list2cmdline([powershell])+' '+arguments+' -Manage'
    values={'DisplayName':'Odessy','DisplayVersion':'0.5.0.dev0','Publisher':'Trisoma','InstallLocation':str(root),'UninstallString':manage_command,'ModifyPath':manage_command,'NoModify':0,'NoRepair':1}
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,key) as handle:
        for name,value in values.items(): winreg.SetValueEx(handle,name,0,winreg.REG_DWORD if isinstance(value,int) else winreg.REG_SZ,value)
    resources={'files':{str(p):digest(p) for p in (launcher,shortcut)},'registry_key':key,'registry_values':values}
    if helper:resources['helper']=str(helper)
    record['windows_registration']=resources
    return resources


def unregister(root, record):
    if os.name!='nt': return []
    import winreg
    root=guard(root)
    resources=record.get('windows_registration',{})
    retained=[]
    # Only exact known paths can be removed, even if the ownership record is edited.
    allowed={str(root/'launch.ps1'),str(Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs'/('Odessy-'+record['id']+'.lnk'))}
    for name,sha in resources.get('files',{}).items():
        if name not in allowed: raise ValueError('invalid registration ownership path')
        path=guard(name)
        if path.is_file() and digest(path)==sha: path.unlink()
        elif path.exists(): retained.append(name)
    key=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\Odessy-'+record['id']
    if resources.get('registry_key')==key:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key) as handle:
                actual={}
                for index in range(winreg.QueryInfoKey(handle)[1]):
                    name,value,_=winreg.EnumValue(handle,index);actual[name]=value
                unchanged=actual==resources.get('registry_values')
            if unchanged: winreg.DeleteKey(winreg.HKEY_CURRENT_USER,key)
            else: retained.append(key)
        except FileNotFoundError: pass
    return retained
