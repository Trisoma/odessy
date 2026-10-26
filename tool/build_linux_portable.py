"""Assemble verified, prebuilt Linux components into an installer-free archive.

This tool never downloads models, installs system packages or executes payloads.
Native compilation and offline wheel installation are owned by linux/build_portable.sh.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.5.0.dev0'
LAUNCHER='''#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
export ODESSY_PORTABLE_ROOT="$HERE"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
unset PYTHONHOME PYTHONPATH
if [ ! -x "$HERE/runtime/bin/python3" ]; then
  echo "Odessy: bundled Python is missing or not executable. Re-extract the matching Linux archive." >&2
  exit 2
fi
exec "$HERE/runtime/bin/python3" -s -m odessy.setup.portable "$@"
'''


def _sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def _private(path):
    return (path.name in ('.env','.npmrc','.pypirc','credentials.json','token.json','configuration.json',
                          'installation.json','trust.sqlite','identity.json')
            or path.name.startswith('.env.') or path.name.endswith(('.key','.p12','.pfx'))
            or any(part in ('.git','.ssh','.aws','.venv') for part in path.parts))


def _copy_tree(source,target):
    source=Path(source).resolve()
    if not source.is_dir():raise ValueError('component directory missing: '+str(source))
    target.mkdir()
    for directory,dirs,names in os.walk(source,followlinks=False):
        for name in [*dirs,*names]:
            path=Path(directory)/name
            relative=path.relative_to(source)
            if _private(relative):raise ValueError('private or development file in payload: '+str(relative))
            if path.is_file() and path.suffix.lower()=='.pem':
                with path.open('rb') as stream:head=stream.read(65536)
                if b'PRIVATE KEY-----' in head:raise ValueError('private key in payload: '+str(relative))
            dest=target/relative
            if path.is_symlink():
                resolved=path.resolve()
                if not resolved.exists() or not resolved.is_relative_to(source):
                    raise ValueError('component link escapes its directory: '+str(relative))
                # Copy the target at its own path; link text must stay relocatable.
                text=os.readlink(path)
                if os.path.isabs(text):raise ValueError('absolute component symlink is not relocatable')
                dest.symlink_to(text,target_is_directory=resolved.is_dir())
            elif path.is_dir():dest.mkdir(exist_ok=True)
            elif path.is_file():shutil.copy2(path,dest)
            else:raise ValueError('special component file refused: '+str(relative))



def _copy_node_runtime(source, target):
    """Ship the runtime, not npm/npx/corepack or user/package-manager config."""
    source = Path(source).resolve()
    binary = (source/'bin/node').resolve()
    if not binary.is_file() or not binary.is_relative_to(source):
        raise ValueError('Node runtime binary is missing or outside its distribution')
    (target/'bin').mkdir(parents=True)
    shutil.copy2(binary, target/'bin/node')
    # The official Node LICENSE includes notices for bundled native components.
    license_path = source/'LICENSE'
    if license_path.is_file() and not license_path.is_symlink():
        shutil.copy2(license_path, target/'LICENSE')


def _elf(path,architecture):
    with path.open('rb') as stream:header=stream.read(20)
    machine={'x86_64':62,'arm64':183}[architecture]
    if len(header)<20 or header[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(header[18:20],'little')!=machine:
        raise ValueError('component ELF architecture does not match '+architecture+': '+path.name)


def build(*,runtime,desktop,node,tools,link,monitor,notices,output,architecture,version=VERSION):
    if architecture not in ('x86_64','arm64'):raise ValueError('unsupported architecture')
    if not version or any(c not in '0123456789abcdefghijklmnopqrstuvwxyz.-' for c in version):raise ValueError('invalid version')
    output=Path(output).absolute()
    if output.exists() or output.is_symlink():raise ValueError('output exists; use a new artifact filename')
    if not output.parent.is_dir():raise ValueError('output parent must exist')
    checksum=output.with_suffix(output.suffix+'.sha256')
    if checksum.exists():raise ValueError('checksum output exists')
    inventory=json.loads((Path(notices)/'inventory.json').read_text(encoding='utf-8'))
    entries=inventory.get('components',[])
    required={'python','desktop','node','tools','link','monitor'}
    if inventory.get('schema_version')!=1 or not required.issubset({e.get('name') for e in entries}):
        raise ValueError('complete component notice inventory required')
    for entry in entries:
        if any(not isinstance(entry.get(k),str) or not entry[k].strip() for k in ('name','version','license','source','notice')):
            raise ValueError('incomplete dependency notice entry')
        notice=Path(entry['notice'])
        if notice.is_absolute() or '..' in notice.parts or not (Path(notices)/notice).is_file():
            raise ValueError('dependency notice text missing')
    with tempfile.TemporaryDirectory(prefix='odessy-linux-build-',dir=output.parent) as temp:
        stage=Path(temp)/f'odessy-{version}-linux-{architecture}';stage.mkdir()
        for name,source in [('runtime',runtime),('desktop',desktop),('node',node),('tools',tools),('link',link),('monitor',monitor),('notices',notices)]:
            if name == "node":_copy_node_runtime(source,stage/name)
            else:_copy_tree(source,stage/name)
        for name in ('runtime/bin/python3','desktop/odessy_desktop','node/bin/node','link/odessy-link','monitor/odessy-monitor'):
            executable=stage/name
            if not executable.is_file():raise ValueError('required binary missing: '+name)
            _elf(executable,architecture)
            executable.chmod(0o755)
        for directory in ('desktop/lib','desktop/data'):
            if not (stage/directory).is_dir():raise ValueError('Flutter release bundle is incomplete')
        if not (stage/'tools/dist/src/server.js').is_file():raise ValueError('compiled Node server missing')
        contract=stage/'src/odessy/execution/contract.json'
        contract.parent.mkdir(parents=True)
        shutil.copy2(ROOT/'src/odessy/execution/contract.json',contract)
        (stage/'Odessy').write_text(LAUNCHER,encoding='utf-8',newline='\n');(stage/'Odessy').chmod(0o755)
        shutil.copy2(ROOT/'installer/assets/application_icon.png',stage/'application_icon.png')
        shutil.copy2(ROOT/'LICENSE',stage/'LICENSE')
        shutil.copy2(ROOT/'THIRD-PARTY-NOTICES.md',stage/'THIRD-PARTY-NOTICES.md')
        shutil.copy2(ROOT/'docs/releases/linux-portable-quickstart.md',stage/'README.md')
        executable_names={'Odessy','runtime/bin/python3','desktop/odessy_desktop','node/bin/node','link/odessy-link','monitor/odessy-monitor'}
        files={}
        for path in sorted(stage.rglob('*')):
            name=path.relative_to(stage).as_posix()
            if path.is_symlink():files[name]={'link':os.readlink(path)}
            elif path.is_file():files[name]={'sha256':_sha(path),'mode':0o755 if name in executable_names or name.startswith(('runtime/bin/','node/bin/')) else stat.S_IMODE(path.stat().st_mode)}
        if len(files)>100000:raise ValueError('portable inventory exceeds 100000 entries')
        manifest=json.dumps({'schema_version':1,'platform':'linux','architecture':architecture,
            'version':version,'files':files},indent=2)
        if len(manifest.encode('utf-8'))>32*1024*1024:raise ValueError('portable manifest exceeds 32 MiB')
        (stage/'portable-manifest.json').write_text(manifest,encoding='utf-8')
        archive=Path(temp)/'artifact.tar.gz'
        def permissions(member):
            member.uid=member.gid=0;member.uname=member.gname=''
            relative=member.name.removeprefix(stage.name+'/')
            if member.isfile() and relative in files:member.mode=files[relative].get('mode',member.mode)
            return member
        with tarfile.open(archive,'w:gz',dereference=False) as tar:tar.add(stage,arcname=stage.name,filter=permissions)
        # Atomic create-only publication; never overwrite a maintainer artifact.
        os.link(archive,output)
        try:
            with checksum.open('x',encoding='ascii') as stream:stream.write(_sha(output)+'  '+output.name+'\n')
        except Exception:
            output.unlink();raise
    return output


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('runtime','desktop','node','tools','link','monitor','notices','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--architecture',choices=('x86_64','arm64'),required=True)
    parser.add_argument('--version',default=VERSION)
    args=vars(parser.parse_args(argv))
    print(build(**args))

if __name__=='__main__':main()
