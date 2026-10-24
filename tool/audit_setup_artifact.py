"""Read-only artifact/version/license inventory. Unknown licenses remain blockers."""
from __future__ import annotations
import argparse
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import zipfile


def audit(path):
    path=Path(path);unknown=[];wheels=[]
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        import stat
        if len(names)!=len(set(names)): raise ValueError('duplicate archive member')
        for entry in archive.infolist():
            name=entry.filename
            if name.startswith('/') or ':' in name or '\\' in name or '..' in Path(name).parts or stat.S_ISLNK(entry.external_attr>>16): raise ValueError('unsafe archive member')
        inventory=json.loads(archive.read('setup-context.json'))['files']
        if set(inventory)!=set(n for n in names if not n.endswith('/') and n!='setup-context.json'): raise ValueError('payload inventory mismatch')
        for name,sha in inventory.items():
            if hashlib.sha256(archive.read(name)).hexdigest()!=sha: raise ValueError('payload checksum mismatch')
        manifest=json.loads(archive.read('wheelhouse/manifest.json'))
        for artifact in manifest['artifacts']:
            raw=archive.read('wheelhouse/'+artifact['file'])
            if hashlib.sha256(raw).hexdigest()!=artifact['sha256']:raise ValueError('artifact checksum mismatch')
            with zipfile.ZipFile(io.BytesIO(raw)) as wheel:
                metadata=BytesParser().parsebytes(wheel.read(next(n for n in wheel.namelist() if n.endswith('.dist-info/METADATA'))))
                license_files=[n for n in wheel.namelist() if any(token in n.lower() for token in ('license','copying','notice'))]
                expression=metadata.get('License-Expression') or metadata.get('License')
                if not expression:unknown.append(artifact['file'])
                wheels.append({'name':metadata['Name'],'version':metadata['Version'],'license':expression,'license_files':license_files})
        product=next(w for w in wheels if w['name']=='odessy')
        if product['version']!='0.5.0.dev0':raise ValueError('unexpected development package version')
        runtime_license=any(n in names for n in ('runtime/LICENSE','runtime/LICENSE.txt'))
        return {'artifact':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'version':product['version'],'wheels':wheels,'unknown_license_metadata':unknown,'python_license_present':runtime_license,'flutter_notices_present':any(n.endswith('NOTICES.Z') for n in names),'publisher_signature_checked':False,'release_accepted':False,'remaining':['Publisher signing/notarization and native OS/device acceptance','Human review of exact dependency license notices and any unknown metadata']}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('artifact');args=parser.parse_args()
    print(json.dumps(audit(args.artifact),indent=2))

