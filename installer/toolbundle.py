"""Developer/release step: build a precompiled Windows tool-server bundle."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
from installer.preflight import REPO_ROOT,default_locations
from installer.process import child_env,owned_directory,run_step,validate_runtimes
from installer.runtime import destination


def source_files(repo: Path) -> list[Path]:
    tools=repo/'tools'
    paths=[tools/name for name in ('package.json','package-lock.json','tsconfig.json')]
    for folder in ('src','tests','vendor'):
        paths.extend(sorted((tools/folder).rglob('*.ts')))
    if any(not p.is_file() or p.is_symlink() for p in paths):
        raise ValueError('Missing or linked tool build input.')
    return paths


def build_bundle(runtime: Path,output_dir: Path,*,repo: Path=REPO_ROOT,run=run_step,validate=validate_runtimes) -> dict:
    versions=validate(runtime)
    files=source_files(repo)
    source_hashes={p.relative_to(repo).as_posix():hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in files}
    package=json.loads((repo/'tools'/'package.json').read_text(encoding='utf-8'))
    lock=json.loads((repo/'tools'/'package-lock.json').read_text(encoding='utf-8'))
    if package['version']!=lock['packages']['']['version']:
        raise ValueError('Tool package and lock versions differ.')
    env=child_env(runtime)
    node=runtime/'node-runtime'/'node.exe'
    npm=runtime/'node-runtime'/'node_modules'/'npm'/'bin'/'npm-cli.js'
    with owned_directory(output_dir) as output, tempfile.TemporaryDirectory(prefix='odessy-tools-build-') as scratch:
        stage=Path(scratch); tools=stage/'tools'; tools.mkdir()
        for source in files:
            target=stage/source.relative_to(repo)
            target.parent.mkdir(parents=True,exist_ok=True)
            data=source.read_bytes()
            if hashlib.sha256(data.replace(b'\r\n',b'\n')).hexdigest()!=source_hashes[source.relative_to(repo).as_posix()]:
                raise ValueError('Tool source changed while copying: '+str(source))
            target.write_bytes(data)
        # Avoid copying user npmrc credentials and running dependency lifecycle scripts.
        user_config=stage/'user.npmrc'; user_config.write_text('',encoding='utf-8')
        global_config=stage/'global.npmrc'; global_config.write_text('',encoding='utf-8')
        print('Installing locked Node dependencies in isolated build directory...',flush=True)
        run([str(node),str(npm),'ci','--include=dev','--ignore-scripts','--no-audit','--no-fund',
             '--userconfig',str(user_config),'--globalconfig',str(global_config),'--cache',str(stage/'npm-cache')],
            cwd=tools,env=env,label='Locked Node dependency installation',timeout=600)
        # Confirm source/lock inputs were not changed by the build or another editor.
        for path,digest in source_hashes.items():
            if hashlib.sha256((repo/path).read_bytes().replace(b'\r\n',b'\n')).hexdigest()!=digest:
                raise ValueError('Tool source changed during preparation: '+path)
        print('Compiling existing tool-server sources...',flush=True)
        run([str(node),str(tools/'node_modules'/'typescript'/'bin'/'tsc'),'-p',str(tools/'tsconfig.json')],
            cwd=tools,env=env,label='Tool server TypeScript compilation')
        run([str(node),'-e',"require('koffi'); import('@modelcontextprotocol/sdk/server/mcp.js').then(()=>console.log('ok'))"],
            cwd=tools,env=env,label='Native dependency and MCP import verification')
        entry=tools/'dist'/'src'/'server.js'
        if not entry.is_file() or not (tools/'node_modules'/'.bin'/'tsc.cmd').is_file():
            raise ValueError('Missing precompiled server or Windows TypeScript shim.')
        # Preserve notices inside the distributable, including native dependency context.
        notices=tools/'_licenses'; notices.mkdir()
        for name in ('LICENSE','THIRD-PARTY-NOTICES.md'):
            (notices/name).write_bytes((repo/name).read_bytes())
        third_party=repo/'THIRD-PARTY-LICENSES'
        if third_party.is_dir():
            for source in third_party.rglob('*'):
                if source.is_symlink():
                    raise ValueError('Linked third-party notice is not accepted.')
                if source.is_file():
                    copied=notices/'THIRD-PARTY-LICENSES'/source.relative_to(third_party)
                    copied.parent.mkdir(parents=True,exist_ok=True)
                    copied.write_bytes(source.read_bytes())
        archive=output/'tool-server.zip'; expanded=0; count=0
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(tools.rglob('*')):
                if path.is_symlink():
                    raise ValueError('A tool bundle must not contain links.')
                if path.is_file():
                    bundle.write(path,path.relative_to(stage).as_posix())
                    expanded+=path.stat().st_size; count+=1
        metadata={'schema_version':1,'platform':'windows-x64','component':'tool-server',
                  'version':package['version'],'archive':'tool-server.zip',
                  'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'size_bytes':archive.stat().st_size,
                  'unpacked_bytes':expanded,'files':count,'runtime_versions':versions,
                  'source_sha256':source_hashes,'precompiled':True,'published':False}
        (output/'tool-server.resources.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
        print('Precompiled bundle created:',archive,flush=True)
    return metadata


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    app,_=default_locations(os.environ,Path.home())
    parser.add_argument('--runtime-dir',type=Path,default=app/'runtime')
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--build',action='store_true')
    args=parser.parse_args()
    try:
        print('Tool-server build inputs:',len(source_files(REPO_ROOT)))
        print('Output:',destination(args.output_dir))
        if not args.build:
            print('PREVIEW ONLY. --build runs npm ci and TypeScript in a scratch directory for release preparation.')
            return 0
        metadata=build_bundle(args.runtime_dir,args.output_dir)
        print(f"Bundle ready: {metadata['size_bytes']} bytes; expanded {metadata['unpacked_bytes']} bytes.")
        print('This is a local release-build artifact, not an uploaded public download.')
        return 0
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        print('Tool bundle build failed:',error)
        return 1
    except KeyboardInterrupt:
        print('Cancelled; owned incomplete bundle removed.')
        return 130

if __name__=='__main__':
    raise SystemExit(main())
