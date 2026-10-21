"""Prepare private Python/Node runtimes; no system PATH or registry changes."""
from __future__ import annotations
import argparse
import json
import os
import platform
import re
import subprocess
from pathlib import Path
from installer.download import DownloadError, download_artifact
from installer.manifest import ManifestError, load_manifest, _unique_keys, validate_https_url
from installer.preflight import default_locations
from installer.space import _verified_local, budget
from installer.unpack import unpack, ArchiveError

DEFAULT_RESOURCES = Path(__file__).resolve().parent/'releases'/'windows-x64-runtimes'/'runtime.resources.json'

class RuntimePreparationError(ValueError):
    pass

def load_release(path: Path):
    data = json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=_unique_keys)
    fields = {'schema_version','platform','download_manifest','download_manifest_sha256','runtimes','sources'}
    if not isinstance(data,dict) or set(data) != fields:
        raise RuntimePreparationError('Invalid runtime resource fields.')
    if type(data['schema_version']) is not int or data['schema_version'] != 1 or data['platform'] != 'windows-x64':
        raise RuntimePreparationError('Unsupported runtime resource schema or platform.')
    manifest = load_manifest(_verified_local(path.parent,data['download_manifest'],data['download_manifest_sha256']))
    artifacts = {a.id:a for a in manifest.artifacts}
    required = {'python-runtime','node-runtime'}
    if set(artifacts) != required or not isinstance(data['runtimes'],dict) or set(data['runtimes']) != required:
        raise RuntimePreparationError('Exactly Python and Node runtime artifacts are required.')
    for name,spec in data['runtimes'].items():
        if not isinstance(spec,dict) or set(spec) != {'format','prefix','unpacked_bytes'}:
            raise RuntimePreparationError('Invalid runtime layout.')
        version = artifacts[name].version
        expected = ('tar.gz','python',r'3\.12\.[0-9]+') if name == 'python-runtime' else ('zip','node-v'+version+'-win-x64',r'22\.[0-9]+\.[0-9]+')
        if spec['format'] != expected[0] or spec['prefix'] != expected[1] or not re.fullmatch(expected[2],version):
            raise RuntimePreparationError('Runtime format, version or root does not match the supported release family.')
        if type(spec['unpacked_bytes']) is not int or spec['unpacked_bytes'] <= 0:
            raise RuntimePreparationError('Measured runtime sizes are required.')
    if not isinstance(data['sources'],dict) or set(data['sources']) != required:
        raise RuntimePreparationError('Upstream checksum-source references are required.')
    for url in data['sources'].values():
        validate_https_url(url)
    return manifest,data


def destination(path: Path) -> Path:
    if not path.is_absolute():
        raise RuntimePreparationError('Runtime directory must be absolute.')
    # Reject existing files/links before resolution. Preparation never replaces installs.
    if path.exists() or path.is_symlink():
        raise RuntimePreparationError('Runtime directory already exists; choose a new directory.')
    target = path.resolve()
    if target == target.parent or target == Path.home().resolve():
        raise RuntimePreparationError('Choose a dedicated application runtime directory.')
    for key in ('WINDIR','SystemRoot','ProgramFiles','ProgramFiles(x86)'):
        if os.environ.get(key):
            protected = Path(os.environ[key]).resolve()
            if target == protected or protected in target.parents:
                raise RuntimePreparationError('Use a per-user runtime directory, outside Windows and Program Files.')
    return target


def probe_runtime(root: Path, name: str, version: str) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.upper() in {'PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','CONDA_PREFIX','NODE_OPTIONS','NODE_PATH'}:
            env.pop(key)
    if name == 'python-runtime':
        command = [str(root/'python.exe'),'-I','-c',
                   'import json,sys,struct; print(json.dumps({"version":".".join(map(str,sys.version_info[:3])),"bits":struct.calcsize("P")*8}))']
    else:
        command = [str(root/'node.exe'),'-p','JSON.stringify({version:process.versions.node,bits:process.arch==="x64"?64:0})']
    result = subprocess.run(command,cwd=root,env=env,capture_output=True,text=True,timeout=30,check=False)
    try:
        report = json.loads(result.stdout)
    except ValueError as error:
        raise RuntimePreparationError('Runtime did not return a valid version report: '+name) from error
    if result.returncode or report != {'version':version,'bits':64}:
        raise RuntimePreparationError('Runtime version/architecture probe failed: '+name)
    return report


def prepare_runtime(manifest,resources: dict,target: Path,cache_dir: Path,*,download=download_artifact,probe=probe_runtime):
    system_arch = os.environ.get('PROCESSOR_ARCHITEW6432') or platform.machine()
    if platform.system() != 'Windows' or system_arch.lower() not in {'amd64','x86_64'}:
        raise RuntimePreparationError('Preparing these runtimes requires Windows x64.')
    target = destination(target)
    layout = {name:{'destination':'app','unpacked_bytes':spec['unpacked_bytes']}
              for name,spec in resources['runtimes'].items()}
    volumes = budget(manifest,layout,cache_dir=cache_dir,app_dir=target,model_dir=target)
    if not all(v['sufficient_for_subset'] for v in volumes):
        raise RuntimePreparationError('Insufficient space for runtime downloads, staging and reserve.')
    # Download/verify first; a failed network request never creates the install path.
    archives = {a.id:download(a,cache_dir).path for a in manifest.artifacts}
    # Do not promote directories out of tempfile.mkdtemp(): Windows mode 0700
    # can retain an administrator-only OWNER RIGHTS DACL after a rename.
    # Create at the final location so normal parent ACL inheritance applies.
    from installer.process import owned_directory
    with owned_directory(target) as stage:
        reports = {}
        for artifact in manifest.artifacts:
            spec = resources['runtimes'][artifact.id]
            folder = stage/artifact.id
            unpack(archives[artifact.id],artifact,folder,kind=spec['format'],prefix=spec['prefix'],unpacked_bytes=spec['unpacked_bytes'])
            reports[artifact.id] = probe(folder,artifact.id,artifact.version)
        # The completion marker is written only after both private probes pass.
        (stage/'odessy-runtime.json').write_text(json.dumps({'schema_version':1,'versions':reports},indent=2),encoding='utf-8')
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    app,_ = default_locations(os.environ,Path.home())
    parser.add_argument('--resources',type=Path,default=DEFAULT_RESOURCES)
    parser.add_argument('--runtime-dir',type=Path,default=app/'runtime')
    parser.add_argument('--cache-dir',type=Path,default=app.parent.parent/'Odessy'/'downloads')
    parser.add_argument('--prepare',action='store_true',help='Download, unpack and run version probes in a new private directory.')
    args = parser.parse_args()
    try:
        manifest,resources = load_release(args.resources)
        target = destination(args.runtime_dir)
        layout = {name:{'destination':'app','unpacked_bytes':spec['unpacked_bytes']}
                  for name,spec in resources['runtimes'].items()}
        volumes = budget(manifest,layout,cache_dir=args.cache_dir,app_dir=target,model_dir=target)
        for artifact in manifest.artifacts:
            print(f'{artifact.id}: {artifact.version}, {artifact.size_bytes} download bytes')
        print('Runtime directory:',target)
        for volume in volumes:
            status = 'OK' if volume['sufficient_for_subset'] else 'FAIL'
            print(f"[{status}] required {volume['required_bytes']/1024**2:.1f} MiB; available {volume['free_bytes']/1024**2:.1f} MiB")
        if not all(v['sufficient_for_subset'] for v in volumes):
            return 1
        if not args.prepare:
            print('PREVIEW ONLY. Add --prepare to create the private runtimes. No installation has occurred.')
            return 0
        def fetch(artifact,cache):
            print('Preparing download:',artifact.id,flush=True)
            return download_artifact(artifact,cache)
        reports = prepare_runtime(manifest,resources,target,args.cache_dir,download=fetch)
        print(json.dumps(reports,indent=2))
        print('Private runtimes ready. System PATH unchanged. Odessy dependencies and application are not installed yet.')
        return 0
    except (OSError,ValueError,DownloadError,subprocess.SubprocessError,ArchiveError) as error:
        print('Runtime preparation failed:',error)
        return 1
    except KeyboardInterrupt:
        print('Cancelled. Staging files cleaned; verified downloads remain cached.')
        return 130

if __name__ == '__main__':
    raise SystemExit(main())
