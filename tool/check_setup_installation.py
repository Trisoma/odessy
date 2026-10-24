"""Opt-in real offline installation/repair/uninstall acceptance in an owned temp dir."""
import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from odessy.setup.maintenance import ManagedInstallation
from odessy.setup.contracts import SetupSelection


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse',required=True)
    args=parser.parse_args()
    bundle=Path(args.wheelhouse).resolve()
    artifacts=[]
    for wheel in sorted(bundle.glob('*.whl')):
        with zipfile.ZipFile(wheel) as archive:
            metadata=BytesParser().parsebytes(archive.read(next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))))
        license_=metadata.get('License-Expression') or metadata.get('License') or 'See wheel METADATA and bundled license files'
        artifacts.append({'file':wheel.name,'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'license':license_})
    (bundle/'manifest.json').write_text(json.dumps({'schema_version':1,'artifacts':artifacts},indent=2),encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='odessy-native-acceptance-') as temporary:
        parent=Path(temporary);root=parent/'runtime';external=parent/'external-model';external.write_bytes(b'preserve')
        manager=ManagedInstallation(root);selection=SetupSelection(role='execution')
        report=manager.install(bundle,selection,manager.plan(bundle,selection)['plan_id'])
        assert report['installed'] and manager.verify()['valid']
        first=manager.record()['active']
        manager.repair(bundle,confirm=manager.plan(bundle,selection)['plan_id'])
        manager.rollback(first,confirm=manager.identity())
        assert manager.verify()['valid']
        (root/'user-settings.txt').write_text('preserve')
        result=manager.uninstall(confirm=manager.identity())
        assert result['removed'] and external.read_bytes()==b'preserve' and (root/'user-settings.txt').read_text()=='preserve'
        print('REAL_OFFLINE_INSTALL_REPAIR_ROLLBACK_UNINSTALL_OK')
    return 0

if __name__=='__main__': raise SystemExit(main())
