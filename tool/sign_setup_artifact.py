"""Explicit native signing with external OS/keychain credentials; never stores keys."""
from __future__ import annotations
import argparse
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit


def sign(artifact,platform,identity,*,timestamp='',notary_profile='',runner=None):
    artifact=Path(artifact).resolve()
    if not artifact.exists() or artifact.is_symlink() or not identity or len(identity)>256 or any(c in identity for c in '\r\n\0'):raise ValueError('existing artifact and explicit signing identity required')
    run=runner or (lambda argv:subprocess.run(argv,check=True,timeout=600))
    if platform=='windows':
        if not re.fullmatch('[A-Fa-f0-9]{40}',identity) or urlsplit(timestamp).scheme!='https':raise ValueError('Windows needs certificate thumbprint and HTTPS timestamp URL')
        run(['signtool.exe','sign','/sha1',identity,'/fd','SHA256','/tr',timestamp,'/td','SHA256',str(artifact)])
        run(['signtool.exe','verify','/pa','/all',str(artifact)])
    elif platform=='macos':
        run(['codesign','--force','--options','runtime','--timestamp','--sign',identity,str(artifact)])
        run(['codesign','--verify','--deep','--strict','--verbose=2',str(artifact)])
        if notary_profile:
            run(['xcrun','notarytool','submit',str(artifact),'--keychain-profile',notary_profile,'--wait'])
            run(['xcrun','stapler','staple',str(artifact)])
    elif platform=='linux':
        signature=str(artifact)+'.asc'
        if Path(signature).exists():raise ValueError('signature exists')
        run(['gpg','--batch','--local-user',identity,'--armor','--detach-sign','--output',signature,str(artifact)])
        run(['gpg','--verify',signature,str(artifact)])
    else:raise ValueError('unsupported signing platform')
    return {'signed':True,'verified':True,'artifact':str(artifact),'platform':platform}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact',required=True);parser.add_argument('--platform',choices=('windows','linux','macos'),required=True)
    parser.add_argument('--identity',required=True);parser.add_argument('--timestamp',default='');parser.add_argument('--notary-profile',default='')
    args=parser.parse_args()
    print(sign(args.artifact,args.platform,args.identity,timestamp=args.timestamp,notary_profile=args.notary_profile))
