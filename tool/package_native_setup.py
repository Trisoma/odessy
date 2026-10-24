"""Wrap a reviewed standalone payload with native packaging tools. No elevation."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import zipfile


def package(payload,output,platform,*,script_signing_thumbprint=""):
    payload,output=Path(payload).resolve(),Path(output).resolve()
    if output.exists() or output.is_symlink():raise ValueError('choose a new artifact output')
    with tempfile.TemporaryDirectory(prefix='odessy-native-package-') as temporary:
        stage=Path(temporary)
        if platform=='windows':
            with zipfile.ZipFile(payload) as archive:
                if 'desktop/odessy_desktop.exe' not in archive.namelist() or 'runtime/python.exe' not in archive.namelist():raise ValueError('Windows payload needs desktop release and bundled Python')
            shutil.copy2(payload,stage/'payload.zip')
            command="$ErrorActionPreference='Stop'; $target=Join-Path $env:LOCALAPPDATA ('Odessy/Setup/'+[guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $target | Out-Null; Expand-Archive -LiteralPath (Join-Path $PSScriptRoot 'payload.zip') -DestinationPath $target; $env:ODESSY_SETUP_PAYLOAD=$target; Start-Process -FilePath (Join-Path $target 'desktop/odessy_desktop.exe') -ArgumentList '--setup'"
            # Use a script file with an ordinary signed-script policy. Do not bypass
            # the user's PowerShell execution policy: release signing is external.
            (stage/'launch.ps1').write_text(command,encoding='utf-8')
            if script_signing_thumbprint:
                import re
                if not re.fullmatch('[a-fA-F0-9]{40}',script_signing_thumbprint):raise ValueError('invalid script signing certificate thumbprint')
                signing="$ErrorActionPreference='Stop'; $c=Get-Item 'Cert:\\CurrentUser\\My\\"+script_signing_thumbprint+"'; $s=Set-AuthenticodeSignature -LiteralPath '"+str(stage/'launch.ps1')+"' -Certificate $c; if($s.Status -ne 'Valid'){throw 'Script signature not valid'}"
                subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',signing],check=True,timeout=30)
            (stage/'launch.cmd').write_text('@echo off\r\npowershell.exe -NoProfile -File "%~dp0launch.ps1"\r\n',encoding='utf-8')
            sed='[Version]\nClass=IEXPRESS\nSEDVersion=3\n[Options]\nPackagePurpose=InstallApp\nShowInstallProgramWindow=1\nHideExtractAnimation=0\nUseLongFileName=1\nInsideCompressed=0\nCAB_FixedSize=0\nCAB_ResvCodeSigning=6144\nRebootMode=N\nInstallPrompt=Open Odessy setup? This preview is not publisher-signed.\nDisplayLicense=\nFinishMessage=\nTargetName={output}\nFriendlyName=Odessy Setup\nAppLaunched=cmd.exe /c launch.cmd\nPostInstallCmd=<None>\nAdminQuietInstCmd=\nUserQuietInstCmd=\nSourceFiles=SourceFiles\n[SourceFiles]\nSourceFiles0={stage}\\\n[SourceFiles0]\n%FILE0%=\n%FILE1%=\n%FILE2%=\n[Strings]\nFILE0="payload.zip"\nFILE1="launch.ps1"\nFILE2="launch.cmd"\n'
            spec=stage/'setup.sed';spec.write_text(sed.format(output=output,stage=stage),encoding='utf-8')
            subprocess.run(['iexpress.exe','/N','/Q',str(spec)],check=True,timeout=180)
            if not output.is_file():raise RuntimeError('native executable packaging failed')
        else:
            with zipfile.ZipFile(payload) as archive:
                for entry in archive.infolist():
                    target=stage/entry.filename
                    if not target.resolve().is_relative_to(stage):raise ValueError('unsafe payload member')
                    archive.extract(entry,stage)
                    if target.is_file() and entry.external_attr>>16:target.chmod((entry.external_attr>>16)&0o777)
            if platform=='macos':subprocess.run(['hdiutil','create','-volname','Odessy Setup','-srcfolder',str(stage),'-format','UDZO',str(output)],check=True,timeout=180)
            elif platform=='linux':
                with tarfile.open(output,'w:gz') as archive:archive.add(stage,arcname='odessy-setup')
            else:raise ValueError('unsupported package platform')
    sha=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name+'.sha256').write_text(sha+'  '+output.name+'\n',encoding='utf-8')
    return output

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script-signing-thumbprint',default='');parser.add_argument('--payload',required=True);parser.add_argument('--output',required=True);parser.add_argument('--platform',choices=('windows','linux','macos'),required=True)
    args=parser.parse_args();print(package(args.payload,args.output,args.platform,script_signing_thumbprint=args.script_signing_thumbprint))
