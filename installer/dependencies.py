"""Install hash-locked wheels into a new private virtual environment."""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit,unquote
from installer.download import download_artifact,DownloadError
from installer.preflight import default_locations
from installer.process import child_env,owned_directory,run_step,validate_runtimes
from installer.runtime import destination
from installer.space import load_resources,budget

DEFAULT_RESOURCES=Path(__file__).resolve().parent/'releases'/'windows-x64-py312'/'python-wheels.resources.json'


def prepare_dependencies(runtime: Path,target: Path,cache: Path,*,resources_path: Path=DEFAULT_RESOURCES,
                         download=download_artifact,run=run_step,validate=validate_runtimes) -> dict:
    manifest,resources=load_resources(resources_path)
    validate(runtime)
    target=destination(target)
    if not all(v['sufficient_for_subset'] for v in budget(manifest,resources['layout'],cache_dir=cache,app_dir=target,model_dir=target,cache_copies=2)):
        raise ValueError('Not enough space for dependency downloads and installation.')
    wheel_files=[]
    for artifact in manifest.artifacts:
        filename=unquote(Path(urlsplit(artifact.url).path).name)
        if Path(filename).name!=filename or not filename.endswith('.whl') or any(c in filename for c in '/\\:'):
            raise ValueError('A dependency artifact must identify a plain wheel filename.')
        print('Verifying wheel:',artifact.id,flush=True)
        wheel_files.append((filename,download(artifact,cache).path))
    env=child_env(runtime)
    interpreter=runtime/'python-runtime'/'python.exe'
    with tempfile.TemporaryDirectory(prefix='.odessy-wheelhouse-',dir=cache) as scratch:
        wheelhouse=Path(scratch)
        for filename,path in wheel_files:
            shutil.copyfile(path,wheelhouse/filename)
        with owned_directory(target) as output:
            venv=output/'venv'
            print('Creating private virtual environment...',flush=True)
            run([str(interpreter),'-I','-m','venv',str(venv)],cwd=output,env=env,label='Virtual environment creation')
            python=venv/'Scripts'/'python.exe'
            lock=resources_path.parent/resources['lock_file']
            print('Installing locked wheels offline...',flush=True)
            run([str(python),'-I','-m','pip','--isolated','--disable-pip-version-check','install',
                 '--no-index','--only-binary=:all:','--no-compile','--require-hashes',
                 '--find-links',str(wheelhouse),'-r',str(lock.resolve())],cwd=output,env=env,label='Locked wheel installation')
            run([str(python),'-I','-m','pip','--isolated','check'],cwd=output,env=env,label='Dependency consistency check')
            expected={a.id:a.version for a in manifest.artifacts}
            code='import importlib.metadata as m,json,re; norm=lambda n:re.sub(r"[-_.]+","-",n).lower(); print(json.dumps({norm(d.metadata["Name"]):d.version for d in m.distributions()}))'
            installed=json.loads(run([str(python),'-I','-c',code],cwd=output,env=env,label='Installed version verification'))
            if any(installed.get(name)!=version for name,version in expected.items()):
                raise ValueError('Installed dependency versions do not match the release lock.')
            run([str(python),'-I','-c','import mcp,jsonschema'],cwd=output,env=env,label='Dependency import smoke test')
            latest_manifest,_=load_resources(resources_path)
            if latest_manifest!=manifest:
                raise ValueError('Dependency release changed during installation.')
            report={'schema_version':1,'runtime_dir':str(runtime.resolve()),'python':str(python),
                    'packages':expected,'relocatable':False}
            (output/'odessy-dependencies.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main() -> int:
    import os
    parser=argparse.ArgumentParser(description=__doc__)
    app,_=default_locations(os.environ,Path.home())
    parser.add_argument('--runtime-dir',type=Path,default=app/'runtime')
    parser.add_argument('--env-dir',type=Path,default=app/'dependencies')
    parser.add_argument('--cache-dir',type=Path,default=app.parent.parent/'Odessy'/'downloads')
    parser.add_argument('--prepare',action='store_true')
    args=parser.parse_args()
    try:
        manifest,resources=load_resources(DEFAULT_RESOURCES)
        target=destination(args.env_dir)
        volumes=budget(manifest,resources['layout'],cache_dir=args.cache_dir,app_dir=target,model_dir=target,cache_copies=2)
        print(f'Locked runtime dependencies: {len(manifest.artifacts)}; download {manifest.download_bytes} bytes')
        print('Private runtime:',args.runtime_dir)
        print('Environment directory:',target)
        if not all(v['sufficient_for_subset'] for v in volumes):
            raise ValueError('Insufficient disk space.')
        if not args.prepare:
            print('PREVIEW ONLY. Add --prepare to install into a new private environment.')
            return 0
        report=prepare_dependencies(args.runtime_dir,target,args.cache_dir)
        print('Private dependencies ready:',len(report['packages']),'locked packages.')
        print('Keep this directory and its base runtime in place; Windows virtual environments are not portable.')
        return 0
    except (OSError,ValueError,DownloadError,subprocess.SubprocessError) as error:
        print('Dependency preparation failed:',error)
        return 1
    except KeyboardInterrupt:
        print('Cancelled; the owned incomplete environment was removed.')
        return 130

if __name__=='__main__':
    raise SystemExit(main())
