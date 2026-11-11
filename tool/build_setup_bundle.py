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
runtime=Path(__file__).resolve().parent / 'runtime'
arguments=list(sys.argv[1:])
if runtime.is_dir(): arguments.extend(['--python-runtime',str(runtime)])
raise SystemExit(main(arguments))
"""


def _harness_files(directory):
    """Verify a staged Harness runtime and return its relative file list.

    The staged directory is the output of ``python -m installer.components.harness
    --prepare``: the declared executable, its sidecars, retained licences and the
    ``harness-runtime.json`` completion marker. Every declared file is re-digested here, so a
    release build cannot package an edited or incomplete runtime. Nothing is downloaded.
    """
    import sys
    sys.path.insert(0, str(ROOT))
    from installer.components.harness import MARKER, verify
    directory = Path(directory).resolve()
    report = verify(directory)
    files = []
    for path in sorted(directory.rglob('*')):
        if path.is_symlink(): raise ValueError('Harness runtime links forbidden')
        if path.is_file(): files.append(path.relative_to(directory).as_posix())
    if MARKER not in files: raise ValueError('Harness runtime is missing its completion marker')
    if len(files) != report['files_verified'] + 1:
        raise ValueError('Harness runtime directory contains undeclared files')
    return directory, files


def _license_for(wheel, declared):
    """The license to record for one wheel, checked against the wheel's own metadata.

    The wheel is the authority: a hand-maintained map may confirm it, and an
    ``UNREVIEWED`` placeholder is replaced by the metadata-derived value, but a map
    entry that contradicts the wheel refuses the build instead of shipping a false
    declaration. A wheel with no readable metadata still needs an explicit value.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from audit_setup_artifact import derive_license
    derived = None
    try:
        with zipfile.ZipFile(wheel) as archive:
            from email.parser import BytesParser
            metadata = BytesParser().parsebytes(archive.read(next(
                n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))))
            derived = derive_license(metadata)[0]
    except (zipfile.BadZipFile, KeyError, StopIteration):
        derived = None
    placeholder = isinstance(declared, str) and 'UNREVIEW' in declared.upper()
    if isinstance(declared, str) and declared.strip() and not placeholder:
        if derived and declared != derived:
            raise ValueError(f'license for {wheel.name} contradicts its metadata: '
                             f'declared {declared}, wheel says {derived}')
        return declared
    if derived:
        return derived
    raise ValueError('license required for every wheel: ' + wheel.name)


def build(wheels, licenses, output, *, runtime=None, desktop=None, node=None, toolserver=None, rust=None, monitor=None, harness=None):
    wheels,licenses,output=Path(wheels),Path(licenses),Path(output)
    if output.exists() or output.is_symlink(): raise ValueError('output exists; choose a new artifact')
    license_map=json.loads(licenses.read_text(encoding='utf-8'))
    artifacts=[]
    for wheel in sorted(wheels.glob('*.whl')):
        if wheel.is_symlink() or not re.fullmatch(r'[A-Za-z0-9_.+-]+\.whl',wheel.name): raise ValueError('invalid wheel')
        license_=_license_for(wheel, license_map.get(wheel.name))
        artifacts.append({'file':wheel.name,'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'license':license_})
    if not any(a['file'].startswith('odessy-') for a in artifacts): raise ValueError('Odessy wheel required')
    if desktop is not None and not any(re.fullmatch(r'keyring-25\.[A-Za-z0-9_.+-]+\.whl', a['file']) for a in artifacts):
        raise ValueError('Desktop payload requires keyring 25.x and its resolved UI dependency wheels.')
    harness_source, harness_members = (_harness_files(harness) if harness is not None else (None, []))
    # Write a private staging archive, then publish create-only.
    import os
    fd,temporary=tempfile.mkstemp(dir=output.parent,suffix='.zip')
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('install.py',BOOTSTRAP)
            if runtime is not None:
                import sys
                sys.path.insert(0,str(ROOT/'src'))
                from odessy.setup.runtime import inventory
                runtime=Path(runtime).resolve(); runtime_data=inventory(runtime)
                archive.writestr('runtime-manifest.json',json.dumps(runtime_data,indent=2))
                for name in runtime_data['files']:archive.write((runtime/name).resolve(),'runtime/'+name)
                archive.writestr('install.cmd','@echo off\r\n"%~dp0runtime\\python.exe" "%~dp0install.py" %*\r\n')
                archive.writestr('install.sh','#!/bin/sh\nset -eu\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nchmod u+x "$HERE/runtime/bin/python3"\nexec "$HERE/runtime/bin/python3" "$HERE/install.py" "$@"\n')
            for prefix, source in (('desktop',desktop),('node',node),('tools',toolserver),('link',rust),('monitor',monitor)):
                if source is None: continue
                for file in Path(source).rglob('*'):
                    if file.is_symlink(): raise ValueError('component links forbidden')
                    if file.is_file():archive.write(file,prefix+'/'+file.relative_to(source).as_posix())
            if toolserver is not None:
                # The reviewed Node tool server resolves the shared execution contract by
                # walking up for `src/odessy/execution/contract.json`. A versioned
                # installation keeps the application source in its private virtualenv, out
                # of that search path, so the tool component carries the same canonical
                # contract file from this source revision (a legacy component layout finds
                # an identical file in `application/src`).
                relative='src/odessy/execution/contract.json'
                if not (Path(toolserver)/relative).is_file():
                    archive.write(ROOT/relative,'tools/'+relative)
            for name in harness_members: archive.write(harness_source/name, 'harness/'+name)
            import sys
            sys.path.insert(0,str(ROOT))
            from installer.build.context import files as installer_files
            for name,data in installer_files(ROOT).items():archive.writestr('bootstrap/'+name,data)
            archive.writestr('bootstrap/odessy/__init__.py','')
            archive.write(ROOT/'src/odessy/credentials.py', 'bootstrap/odessy/credentials.py')
            archive.write(ROOT/'src/odessy/model_providers.py', 'bootstrap/odessy/model_providers.py')
            for module in (ROOT/'src/odessy/setup').glob('*.py'):
                archive.write(module,'bootstrap/odessy/setup/'+module.name)
            # The voice artifact manifest is data, not code, but the helper reads
            # it by path at install time. Omitting it would make the packaged
            # installer unable to offer voice at all.
            archive.write(ROOT/'src/odessy/setup/voice-artifacts.json',
                          'bootstrap/odessy/setup/voice-artifacts.json')
            # Progress runs before the application wheel is installed. Include
            # its shared stdlib-only redactor in the standalone bootstrap too.
            for name in ('__init__.py', 'redaction.py'):
                archive.write(ROOT/'src/odessy/authority'/name,
                              'bootstrap/odessy/authority/'+name)
            archive.write(ROOT/'LICENSE','LICENSE')
            archive.write(ROOT/'THIRD-PARTY-NOTICES.md','THIRD-PARTY-NOTICES.md')
            archive.writestr('wheelhouse/manifest.json',json.dumps({'schema_version':1,'artifacts':artifacts},indent=2))
            for artifact in artifacts: archive.write(wheels/artifact['file'],'wheelhouse/'+artifact['file'])
        with zipfile.ZipFile(temporary,'a') as archive:
            inventory={name:hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist() if not name.endswith('/')}
            archive.writestr('setup-context.json',json.dumps({'schema_version':1,'components':['python']+[name for name,source in (('desktop',desktop),('node',node),('tools',toolserver),('link',rust),('monitor',monitor),('harness',harness)) if source is not None],'files':inventory},indent=2))
        os.link(temporary,output)
    finally: Path(temporary).unlink(missing_ok=True)
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse',required=True)
    parser.add_argument('--licenses',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--python-runtime')
    parser.add_argument('--desktop-bundle')
    parser.add_argument('--node-runtime')
    parser.add_argument('--toolserver')
    parser.add_argument('--rust')
    parser.add_argument('--monitor')
    parser.add_argument('--harness-runtime',help='staged Harness runtime directory from installer.components.harness')
    args=parser.parse_args()
    print(build(args.wheelhouse,args.licenses,args.output,runtime=args.python_runtime,desktop=args.desktop_bundle,node=args.node_runtime,toolserver=args.toolserver,rust=args.rust,monitor=args.monitor,harness=args.harness_runtime))



