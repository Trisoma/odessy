"""Build an offline, Python-required bootstrap archive; no download during install."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
BOOTSTRAP = """from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / 'bootstrap'))
from odessy.setup.manage import main
raise SystemExit(main())
"""


def build(wheels, licenses, output):
    wheels,licenses,output=Path(wheels),Path(licenses),Path(output)
    if output.exists() or output.is_symlink(): raise ValueError('output exists; choose a new artifact')
    license_map=json.loads(licenses.read_text(encoding='utf-8'))
    artifacts=[]
    for wheel in sorted(wheels.glob('*.whl')):
        if wheel.is_symlink() or not re.fullmatch(r'[A-Za-z0-9_.+-]+\.whl',wheel.name): raise ValueError('invalid wheel')
        license_=license_map.get(wheel.name)
        if not isinstance(license_,str) or not license_.strip(): raise ValueError('license required for every wheel')
        artifacts.append({'file':wheel.name,'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'license':license_})
    if not any(a['file'].startswith('odessy-') for a in artifacts): raise ValueError('Odessy wheel required')
    # Write a private staging archive, then publish create-only.
    import os
    fd,temporary=tempfile.mkstemp(dir=output.parent,suffix='.zip')
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('install.py',BOOTSTRAP)
            archive.writestr('bootstrap/odessy/__init__.py','')
            for module in (ROOT/'src/odessy/setup').glob('*.py'):
                archive.write(module,'bootstrap/odessy/setup/'+module.name)
            archive.write(ROOT/'LICENSE','LICENSE')
            archive.write(ROOT/'THIRD-PARTY-NOTICES.md','THIRD-PARTY-NOTICES.md')
            archive.writestr('wheelhouse/manifest.json',json.dumps({'schema_version':1,'artifacts':artifacts},indent=2))
            for artifact in artifacts: archive.write(wheels/artifact['file'],'wheelhouse/'+artifact['file'])
        os.link(temporary,output)
    finally: Path(temporary).unlink(missing_ok=True)
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse',required=True)
    parser.add_argument('--licenses',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(build(args.wheelhouse,args.licenses,args.output))
