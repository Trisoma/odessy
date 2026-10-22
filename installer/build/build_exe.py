"""Build a standalone Windows x64 wizard using an isolated PyInstaller environment."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from installer.components.application import source_snapshot, validate_bundle
from installer.core.download import _reject_link
from installer.core.preflight import REPO_ROOT
from installer.core.process import owned_directory
from installer.components.toolbundle import source_files

PYINSTALLER_VERSION = '6.22.3'


def build_inputs(repo: Path, bundle: Path):
    validate_bundle(bundle, repo)
    files = source_snapshot(repo)
    for path in source_files(repo): files[path.relative_to(repo).as_posix()] = path.read_bytes()
    for path in sorted((repo / 'installer').rglob('*')):
        if '__pycache__' in path.parts: continue
        if path.is_file() and (path.suffix in {'.py', '.json', '.txt', '.manifest', '.ps1', '.ico'} or path.name == 'README.md'):
            _reject_link(path); files[path.relative_to(repo).as_posix()] = path.read_bytes()
    for name in ('tool-server.zip', 'tool-server.resources.json'):
        files['bundled-tools/' + name] = (bundle / name).read_bytes()
    return files


def build(bundle: Path, output: Path, *, repo=REPO_ROOT):
    if platform.system() != 'Windows' or sys.maxsize <= 2**32:
        raise ValueError('Build on Windows with a 64-bit Python including Tkinter.')
    import tkinter
    if not output.is_absolute(): raise ValueError('Output directory must be absolute.')
    _reject_link(output)
    files = build_inputs(repo, bundle)
    with owned_directory(output) as result, tempfile.TemporaryDirectory(prefix='odessy-exe-build-') as temporary:
        scratch = Path(temporary); stage = scratch / 'inputs'; stage.mkdir()
        for name, data in files.items():
            path = stage / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        environment = scratch / 'builder'; venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / 'Scripts/python.exe'
        # Builder dependencies are confined to this temporary venv, never application dependencies.
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--no-cache-dir',
                        '--only-binary=:all:', '--index-url', 'https://pypi.org/simple',
                        '-r', str(stage / 'installer/releases/windows-x64-bootstrap/build-requirements.txt')], check=True, timeout=300)
        license_path = Path(sys.base_prefix) / 'LICENSE.txt'
        if not license_path.is_file(): raise ValueError('Missing build interpreter license notice.')
        files['_bootstrap-notices/Python-LICENSE.txt'] = license_path.read_bytes()
        tk_license = Path(sys.base_prefix) / 'tcl/tk8.6/license.terms'
        if tk_license.is_file(): files['_bootstrap-notices/Tk-license.terms'] = tk_license.read_bytes()
        notice_code = ('import importlib.metadata as m,json; d=m.distribution("pyinstaller"); '
                       'print(json.dumps({str(p):d.locate_file(p).read_bytes().hex() for p in d.files '
                       'if "licenses" in p.parts and d.locate_file(p).is_file()}))')
        notices = json.loads(subprocess.check_output([str(python), '-c', notice_code], text=True, encoding='utf-8'))
        if not notices: raise ValueError('Missing PyInstaller license notices.')
        for name, content in notices.items():
            files['_bootstrap-notices/PyInstaller-' + Path(name).name] = bytes.fromhex(content)
        data = scratch / 'payload'; data.mkdir()
        for name, content in files.items():
            path = data / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
        command = [str(python), '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
                   '--noupx', '--manifest', str(stage / 'installer/windows.manifest'),
                   '--icon', str(stage / 'installer/assets/odessy-boat.ico'),
                   '--name', 'OdessySetup', '--paths', str(stage), '--add-data', str(data) + os.pathsep + '.',
                   '--distpath', str(scratch / 'dist'), '--workpath', str(scratch / 'work'),
                   '--specpath', str(scratch), str(stage / 'installer/exe_entry.py')]
        subprocess.run(command, cwd=scratch, check=True, timeout=600)
        executable = scratch / 'dist/OdessySetup.exe'
        shutil.copyfile(executable, result / executable.name)
        packages = subprocess.check_output([str(python), '-m', 'pip', 'list', '--format=json'], text=True, encoding='utf-8')
        metadata = dict(schema_version=1, executable=executable.name, platform='windows-x64',
                        pyinstaller=PYINSTALLER_VERSION, builder_python=platform.python_version(),
                        sha256=hashlib.sha256(executable.read_bytes()).hexdigest(), size_bytes=executable.stat().st_size,
                        input_sha256={name:hashlib.sha256(content).hexdigest() for name,content in files.items()},
                        builder_packages=json.loads(packages), clean_machine_verified=False, installation_ready=False)
        (result / 'build-report.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return metadata


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool-bundle',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,default=REPO_ROOT/'dist/installer')
    parser.add_argument('--build',action='store_true')
    args=parser.parse_args(argv)
    if not args.build:
        files=build_inputs(REPO_ROOT,args.tool_bundle)
        print('EXE build preview:',len(files),'inputs;',sum(map(len,files.values())),'payload bytes')
        print('Add --build. This prepares an isolated builder, not an application installation.')
        return 0
    report=build(args.tool_bundle,args.output_dir)
    print('EXE:',args.output_dir/report['executable'])
    print('SHA-256:',report['sha256']); return 0


if __name__=='__main__': raise SystemExit(main())
