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
            if script_signing_thumbprint:
                raise ValueError('NSIS has no PowerShell launcher; sign the final EXE using sign_setup_artifact.py')
            try:
                from tool.windows_nsis import build
            except ModuleNotFoundError:
                from windows_nsis import build
            build(payload,output,stage)
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
