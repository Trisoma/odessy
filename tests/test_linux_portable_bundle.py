import hashlib
import json
import tarfile
from pathlib import Path
import pytest
from tool.build_linux_portable import build


def inputs(tmp_path):
    paths={}
    for name, rel in {'runtime':'bin/python3','desktop':'odessy_desktop','node':'bin/node',
                      'link':'odessy-link','monitor':'odessy-monitor','tools':'dist/src/server.js'}.items():
        base=tmp_path/name; path=base/rel;path.parent.mkdir(parents=True,exist_ok=True)
        binary=bytearray(64);binary[:4]=b'\x7fELF';binary[4:6]=b'\x02\x01';binary[18:20]=(62).to_bytes(2,'little')
        path.write_bytes(b'console.log(1)' if name=='tools' else binary);paths[name]=base
    (paths['desktop']/'lib').mkdir();(paths['desktop']/'data').mkdir()
    notices=tmp_path/'notices';notices.mkdir();(notices/'LICENSES.txt').write_text('Test notices')
    (notices/'inventory.json').write_text(json.dumps({'schema_version':1,'components':[
        {'name':name,'version':'test','license':'MIT','source':'https://example.test/'+name,'notice':'LICENSES.txt'}
        for name in ('python','desktop','node','tools','link','monitor')]}))
    paths['notices']=notices
    return paths


def test_linux_archive_has_relocatable_launchers_modes_and_hashes(tmp_path):
    paths=inputs(tmp_path);out=tmp_path/'release.tar.gz'
    build(output=out,architecture='x86_64',**paths)
    with tarfile.open(out) as tar:
        members={m.name:m for m in tar.getmembers()}
        prefix='odessy-0.5.0.dev0-linux-x86_64/'
        assert prefix+'src/odessy/execution/contract.json' in members
        assert members[prefix+'Odessy'].mode & 0o111
        assert members[prefix+'runtime/bin/python3'].mode & 0o111
        manifest=json.load(tar.extractfile(prefix+'portable-manifest.json'))
        assert 'ODESSY_API_TOKEN' not in json.dumps(manifest)
        for name,entry in manifest['files'].items():
            assert hashlib.sha256(tar.extractfile(prefix+name).read()).hexdigest()==entry['sha256']
    assert out.with_suffix(out.suffix+'.sha256').is_file()


def test_builder_refuses_wrong_arch_secrets_and_overwrite(tmp_path):
    paths=inputs(tmp_path);out=tmp_path/'release.tar.gz'
    with pytest.raises(ValueError,match='architecture'):
        build(output=out,architecture='arm64',**paths)
    (paths['runtime']/'.env').write_text('secret')
    with pytest.raises(ValueError,match='private'):
        build(output=out,architecture='x86_64',**paths)
    (paths['runtime']/'.env').unlink()
    out.write_bytes(b'preserve')
    with pytest.raises(ValueError,match='exists'):
        build(output=out,architecture='x86_64',**paths)
    assert out.read_bytes()==b'preserve'

def test_public_ca_bundle_allowed_but_private_pem_refused(tmp_path):
    paths=inputs(tmp_path)
    pem=paths['runtime']/'cacert.pem'
    pem.write_text('-----BEGIN CERTIFICATE-----\npublic certificate\n')
    build(output=tmp_path/'public.tar.gz',architecture='x86_64',**paths)
    pem.write_text('-----BEGIN PRIVATE KEY-----\nsecret\n')
    with pytest.raises(ValueError,match='private'):
        build(output=tmp_path/'private.tar.gz',architecture='x86_64',**paths)

def test_node_payload_is_runtime_only_and_excludes_npm_credentials(tmp_path):
    paths=inputs(tmp_path)
    vendor=paths['node']/'lib/node_modules/npm';vendor.mkdir(parents=True)
    (vendor/'.npmrc').write_text('//registry.example/:_authToken=never-bundle')
    (paths['node']/'bin/npm').write_text('development tool')
    (paths['node']/'bin/npx').write_text('development tool')
    (paths['node']/'LICENSE').write_text('Node license text')
    out=tmp_path/'node-only.tar.gz';build(output=out,architecture='x86_64',**paths)
    with tarfile.open(out) as tar:
        names=tar.getnames();prefix='odessy-0.5.0.dev0-linux-x86_64/'
        assert prefix+'node/bin/node' in names
        assert prefix+'node/LICENSE' in names
        assert not any(n.endswith(('.npmrc','/npm','/npx')) or '/node/lib/' in n for n in names)
