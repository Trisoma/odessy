"""Read-only artifact/version/license inventory. Unknown licenses remain blockers."""
from __future__ import annotations
import argparse
from email.parser import BytesParser
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

#: Entry points of the retired Tkinter/PyInstaller installer. The shipping setup is the
#: Flutter desktop application launched by the NSIS bootstrap, so a payload that still
#: carries these files was produced by the wrong build path and is refused.
REMOVED_INSTALLER_FILES = (
    'installer/wizard.py',
    'installer/launcher.py',
    'installer/maintenance_ui.py',
    'installer/uninstall_ui.py',
    'installer/start.py',
    'installer/exe_entry.py',
    'installer/entrypoints.py',
    'installer/windows.manifest',
    'installer/build/build_exe.py',
    'installer/build/bootstrap.py',
)
REMOVED_INSTALLER_PREFIXES = ('installer/ui/',)

#: The one setup entry point the native bootstrap launches.
SETUP_ENTRIES = ('desktop/odessy_desktop.exe', 'desktop/odessy_desktop')

#: Unambiguous OSI classifier names and the SPDX id they license. An ambiguous
#: classifier (`BSD License`) is reported as the classifier text, never guessed
#: into one BSD variant.
CLASSIFIER_LICENSES = {
    'MIT License': 'MIT',
    'Apache Software License': 'Apache-2.0',
    'Python Software Foundation License': 'PSF-2.0',
    'ISCL': 'ISC',
}


def derive_license(metadata):
    """Return ``(license, source)`` for one wheel's METADATA, or ``(None, 'none')``.

    Precedence is the standard the packaging metadata itself gives: an SPDX
    ``License-Expression``, a short legacy ``License`` value, then an OSI classifier.
    A wheel that ships only a ``License-File`` is *not* guessed from the file name.
    """
    expression = (metadata.get('License-Expression') or '').strip()
    if expression:
        return expression, 'metadata-expression'
    legacy = (metadata.get('License') or '').strip()
    if legacy and len(legacy) <= 120 and re.fullmatch(r'[A-Za-z0-9.+\- ]+', legacy):
        return legacy, 'metadata-expression'
    for classifier in metadata.get_all('Classifier') or ():
        match = re.fullmatch(r'License :: OSI Approved :: (.+)', classifier.strip())
        if not match:
            continue
        name = match.group(1).strip()
        if name in CLASSIFIER_LICENSES:
            return CLASSIFIER_LICENSES[name], 'metadata-classifier'
        if name not in ('License',) and not name.startswith('License'):
            return name, 'metadata-classifier'
    return None, 'none'


def removed_installer_files(names):
    """Payload members that belong to the removed installer path."""
    found = []
    for name in names:
        if not name.startswith('bootstrap/installer/'):
            continue
        relative = name[len('bootstrap/'):]
        if relative in REMOVED_INSTALLER_FILES or any(
                relative.startswith(prefix) for prefix in REMOVED_INSTALLER_PREFIXES):
            found.append(name)
    return found


def audit(path):
    path=Path(path);unknown=[];wheels=[]
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        import stat
        if len(names)!=len(set(names)): raise ValueError('duplicate archive member')
        for entry in archive.infolist():
            name=entry.filename
            if name.startswith('/') or ':' in name or '\\' in name or '..' in Path(name).parts or stat.S_ISLNK(entry.external_attr>>16): raise ValueError('unsafe archive member')
        removed = removed_installer_files(names)
        if removed:
            raise ValueError('payload carries the removed Tkinter/PyInstaller installer path: '
                             + ', '.join(sorted(removed)))
        inventory=json.loads(archive.read('setup-context.json'))['files']
        if set(inventory)!=set(n for n in names if not n.endswith('/') and n!='setup-context.json'): raise ValueError('payload inventory mismatch')
        for name,sha in inventory.items():
            if hashlib.sha256(archive.read(name)).hexdigest()!=sha: raise ValueError('payload checksum mismatch')
        if any(name.startswith('desktop/') for name in names):
            entry = next((name for name in SETUP_ENTRIES if name in names), None)
            if entry is None:
                raise ValueError('payload declares a desktop component without the Flutter setup entry point')
            if 'bootstrap/odessy/setup/bridge.py' not in names:
                raise ValueError('payload lacks the setup bridge the desktop setup calls')
        manifest=json.loads(archive.read('wheelhouse/manifest.json'))
        for artifact in manifest['artifacts']:
            raw=archive.read('wheelhouse/'+artifact['file'])
            if hashlib.sha256(raw).hexdigest()!=artifact['sha256']:raise ValueError('artifact checksum mismatch')
            with zipfile.ZipFile(io.BytesIO(raw)) as wheel:
                metadata=BytesParser().parsebytes(wheel.read(next(n for n in wheel.namelist() if n.endswith('.dist-info/METADATA'))))
                license_files=[n for n in wheel.namelist() if any(token in n.lower() for token in ('license','copying','notice'))]
                license_, source = derive_license(metadata)
                if not license_:unknown.append(artifact['file'])
                wheels.append({'name':metadata['Name'],'version':metadata['Version'],'license':license_,
                               'license_source':source,'license_files':license_files})
        product=next(w for w in wheels if w['name']=='odessy')
        if product['version']!='0.5.0.dev0':raise ValueError('unexpected development package version')
        runtime_license=any(n in names for n in ('runtime/LICENSE','runtime/LICENSE.txt'))
        return {'artifact':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'version':product['version'],
                'components':json.loads(archive.read('setup-context.json'))['components'],
                'setup_entry':next((name for name in SETUP_ENTRIES if name in names), None),
                'wheels':wheels,'unknown_license_metadata':unknown,'python_license_present':runtime_license,
                'flutter_notices_present':any(n.endswith('NOTICES.Z') for n in names),'publisher_signature_checked':False,
                'release_accepted':False,'remaining':['Publisher signing/notarization and native OS/device acceptance',
                'Owner acceptance of the distributed license set (a publisher decision, not a metadata gap)']}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('artifact');args=parser.parse_args()
    print(json.dumps(audit(args.artifact),indent=2))
