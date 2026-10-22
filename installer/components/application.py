"""Assemble a runnable source-layout application from validated private components."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from installer.components.dependencies import DEFAULT_RESOURCES as DEPENDENCY_RESOURCES
from installer.core.download import DownloadError
from installer.core.manifest import _unique_keys
from installer.core.preflight import REPO_ROOT,default_locations
from installer.core.process import child_env,owned_directory,run_step,validate_runtimes
from installer.components.runtime import destination
from installer.core.space import load_resources,volume_for,RESERVE_BYTES
from installer.components.toolbundle import source_files as tool_source_files
from installer.core.unpack import unpack


def text_hash(data: bytes) -> str:
    return hashlib.sha256(data.replace(b'\r\n',b'\n')).hexdigest()


def source_snapshot(repo: Path) -> dict[str,bytes]:
    paths=[repo/name for name in ('pyproject.toml','README.md','LICENSE','THIRD-PARTY-NOTICES.md','config/odessy.env')]
    for folder in ('src/odessy','policy','THIRD-PARTY-LICENSES'):
        for path in sorted((repo/folder).rglob('*')):
            if '__pycache__' in path.parts or path.suffix in {'.pyc','.pyo'}:
                continue
            if path.is_symlink():
                raise ValueError('Application source contains a file link.')
            if path.is_file():
                if folder=='src/odessy' and path.suffix not in {'.py','.json','.toml','.txt','.md'}:
                    raise ValueError('Unreviewed source asset type: '+str(path))
                paths.append(path)
    if not (repo/'src'/'odessy'/'__main__.py').is_file():
        raise ValueError('Missing application source entry point.')
    result={}
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError('Missing or linked application input: '+str(path))
        result[path.relative_to(repo).as_posix()]=path.read_bytes()
    return result


@dataclass(frozen=True)
class LocalBundle:
    version: str
    size_bytes: int
    sha256: str


def validate_bundle(folder: Path,repo: Path=REPO_ROOT):
    data=json.loads((folder/'tool-server.resources.json').read_text(encoding='utf-8'),object_pairs_hook=_unique_keys)
    fields={'schema_version','platform','component','version','archive','sha256','size_bytes','unpacked_bytes',
            'files','runtime_versions','source_sha256','precompiled','published'}
    if not isinstance(data,dict) or set(data)!=fields or type(data['schema_version']) is not int or data['schema_version']!=1:
        raise ValueError('Invalid prebuilt tool resource metadata.')
    if data['platform']!='windows-x64' or data['component']!='tool-server' or data['precompiled'] is not True:
        raise ValueError('Expected a precompiled Windows x64 tool bundle.')
    if data['archive']!='tool-server.zip' or not isinstance(data['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',data['sha256']):
        raise ValueError('Invalid tool archive reference.')
    for name in ('size_bytes','unpacked_bytes','files'):
        if type(data[name]) is not int or data[name]<=0:
            raise ValueError('Tool resource sizes/counts must be positive integers.')
    expected_version=json.loads((repo/'tools'/'package.json').read_text(encoding='utf-8'))['version']
    expected_hashes={p.relative_to(repo).as_posix():text_hash(p.read_bytes()) for p in tool_source_files(repo)}
    if data['version']!=expected_version or data['source_sha256']!=expected_hashes:
        raise ValueError('Tool bundle was built from different source/lock inputs; rebuild it.')
    path=folder/data['archive']
    if path.is_symlink() or path.stat().st_size!=data['size_bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=data['sha256']:
        raise ValueError('Tool bundle archive hash or size does not match metadata.')
    return path,data


def validate_dependencies(runtime: Path,dependencies: Path,*,repo: Path=REPO_ROOT,run=run_step) -> dict:
    manifest,_=load_resources(DEPENDENCY_RESOURCES,repo)
    expected={a.id:a.version for a in manifest.artifacts}
    data=json.loads((dependencies/'odessy-dependencies.json').read_text(encoding='utf-8'),object_pairs_hook=_unique_keys)
    python=dependencies/'venv'/'Scripts'/'python.exe'
    fields={'schema_version','runtime_dir','python','packages','relocatable'}
    if (not isinstance(data,dict) or set(data)!=fields or type(data.get('schema_version')) is not int
            or data.get('schema_version')!=1 or data.get('packages')!=expected
            or Path(data['runtime_dir']).resolve()!=runtime.resolve() or Path(data['python']).resolve()!=python.resolve()):
        raise ValueError('Dependency environment is moved, incomplete or built for a different release.')
    code='import importlib.metadata as m,json,re; norm=lambda n:re.sub(r"[-_.]+","-",n).lower(); print(json.dumps({norm(d.metadata["Name"]):d.version for d in m.distributions()}))'
    actual=json.loads(run([str(python),'-I','-c',code],cwd=dependencies,env=child_env(runtime),label='Dependency version verification'))
    if any(actual.get(name)!=version for name,version in expected.items()):
        raise ValueError('Actual private dependencies no longer match the release lock.')
    return {'python':str(python.resolve()),'packages':expected}


def assemble(runtime: Path,dependencies: Path,bundle_dir: Path,target: Path,*,repo: Path=REPO_ROOT,
             run=run_step,validate=validate_runtimes,dependency_check=validate_dependencies) -> dict:
    for path in (runtime,dependencies,bundle_dir):
        if not path.is_absolute():
            raise ValueError('Component paths must be absolute.')
    target=destination(target)
    versions=validate(runtime)
    dep=dependency_check(runtime,dependencies,repo=repo,run=run)
    archive,tool=validate_bundle(bundle_dir,repo)
    if tool['runtime_versions']!=versions:
        raise ValueError('Tool bundle runtime versions differ from the selected runtimes.')
    snapshot=source_snapshot(repo)
    payload=sum(map(len,snapshot.values()))+tool['unpacked_bytes']
    required=payload*2+max(RESERVE_BYTES,(payload*2+9)//10)
    if volume_for(target).free_bytes<required:
        raise ValueError('Insufficient space for application assembly and reserve.')
    with owned_directory(target) as output:
        for relative,data in snapshot.items():
            path=output/relative; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
        print('Extracting verified precompiled tools...',flush=True)
        unpack(archive,LocalBundle(tool['version'],tool['size_bytes'],tool['sha256']),output/'tools',
               kind='zip',prefix='tools',unpacked_bytes=tool['unpacked_bytes'],preserve_timestamps=True)
        if len([p for p in (output/'tools').rglob('*') if p.is_file()])!=tool['files']:
            raise ValueError('Unpacked tool file count differs from metadata.')
        for relative,digest in tool['source_sha256'].items():
            if text_hash((output/relative).read_bytes())!=digest:
                raise ValueError('Unpacked tool source differs from its release snapshot.')
        inputs=[output/p for p in tool['source_sha256']]
        entry=output/'tools'/'dist'/'src'/'server.js'
        if not entry.is_file() or entry.stat().st_mtime<max(p.stat().st_mtime for p in inputs):
            raise ValueError('Prebuilt tool entry is missing or older than its source inputs.')
        template=Path(__file__).resolve().parents[1]/'templates'/'launch.py'
        (output/'launch.py').write_bytes(template.read_bytes())
        (output/'start-odessy.ps1').write_text(
            '$ErrorActionPreference = "Stop"\n'
            '$settings = Get-Content -LiteralPath (Join-Path $PSScriptRoot "odessy-application.json") -Raw | ConvertFrom-Json\n'
            '$payload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject @($args) -Compress)))\n'
            '& $settings.python -I (Join-Path $PSScriptRoot "launch.py") --encoded-args $payload\n'
            'exit $LASTEXITCODE\n',encoding='utf-8')
        settings={'schema_version':1,'status':'ready','runtime_dir':str(runtime.resolve()),
                  'dependency_dir':str(dependencies.resolve()),'python':dep['python'],'runtime_versions':versions,
                  'source_sha256':{name:text_hash(data) for name,data in snapshot.items()},'tool_sha256':tool['sha256']}
        (output/'odessy-application.json').write_text(json.dumps(settings,indent=2),encoding='utf-8')
        env=child_env(runtime)
        command=[dep['python'],'-I',str(output/'launch.py')]
        print('Checking assembled application startup...',flush=True)
        with tempfile.TemporaryDirectory(prefix='odessy-application-smoke-') as scratch:
            workspace=Path(scratch); (workspace/'launcher-smoke.txt').write_text('fixture',encoding='utf-8')
            smoke_env={key:value for key,value in env.items() if not key.upper().startswith('ODESSY_')}
            smoke_config=workspace/'smoke.env'; smoke_config.write_text('ODESSY_DEPLOYMENT=single\n',encoding='utf-8')
            smoke_env.update(ODESSY_CONFIG_FILE=str(smoke_config),ODESSY_SERVERS_FILE=str(workspace/'no-servers.toml'),
                             ODESSY_COMMANDS_FILE=str(workspace/'no-commands.toml'))
            run(command+['--','--help'],cwd=workspace,env=smoke_env,label='Installed CLI help')
            result=run(command+['--workspace',str(workspace),'--','--fake-model','list .'],
                       cwd=workspace,env=smoke_env,label='Private Node tool-server smoke',timeout=90)
            if 'launcher-smoke.txt' not in result:
                raise ValueError('Assembled application did not successfully list the isolated workspace.')
        # Catch a concurrent edit instead of declaring a mixed source snapshot ready.
        validate_bundle(bundle_dir,repo)
        if source_snapshot(repo)!=snapshot:
            raise ValueError('Application source changed during assembly.')
    return settings


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    app,_=default_locations(os.environ,Path.home())
    parser.add_argument('--runtime-dir',type=Path,default=app/'runtime')
    parser.add_argument('--env-dir',type=Path,default=app/'dependencies')
    parser.add_argument('--tool-bundle',type=Path,required=True)
    parser.add_argument('--app-dir',type=Path,default=app/'application')
    parser.add_argument('--prepare',action='store_true')
    args=parser.parse_args()
    try:
        target=destination(args.app_dir)
        archive,tool=validate_bundle(args.tool_bundle)
        source=source_snapshot(REPO_ROOT)
        print('Application source files:',len(source))
        print('Verified precompiled bundle:',archive)
        print('Application directory:',target)
        if not args.prepare:
            print('PREVIEW ONLY. --prepare connects private components, copies source, and tests a temporary workspace.')
            return 0
        settings=assemble(args.runtime_dir,args.env_dir,args.tool_bundle,target)
        print('Application ready:',target)
        print('Private interpreter:',settings['python'])
        print('Use start-odessy.ps1 --workspace <folder> -- <existing Odessy options>.')
        print('Real model, voice, graphical installer and shortcuts are not configured by this block.')
        return 0
    except (OSError,ValueError,KeyError,TypeError,DownloadError,subprocess.SubprocessError) as error:
        print('Application assembly failed:',error)
        return 1
    except KeyboardInterrupt:
        print('Cancelled; owned incomplete application directory cleaned.')
        return 130

if __name__=='__main__':
    raise SystemExit(main())
