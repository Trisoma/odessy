import importlib.util
import json
import sys
import zipfile
from pathlib import Path
import pytest


def builder():
    path = Path(__file__).parents[1] / 'tool'
    sys.path.insert(0, str(path))
    try:
        spec = importlib.util.spec_from_file_location('executor_package', path/'package_executor.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(path))


def test_executor_uses_existing_builder_and_no_desktop(tmp_path):
    wheels=tmp_path/'wheels'; wheels.mkdir()
    for name in ('odessy','zeroconf','ifaddr'):
        with zipfile.ZipFile(wheels/f'{name}-1.0-py3-none-any.whl','w') as archive:
            archive.writestr(f'{name}-1.0.dist-info/METADATA', f'Name: {name}\nVersion: 1.0\nLicense-Expression: MIT\n')
            if name=='odessy':
                archive.writestr('odessy/application/executor.py', '# fixture')
                archive.writestr('odessy-1.0.dist-info/entry_points.txt','[console_scripts]\nodessy-executor = odessy.application.executor:main\n')
    licenses=tmp_path/'licenses.json';licenses.write_text('{}')
    node=tmp_path/'node';node.mkdir();(node/'node.exe').write_bytes(b'fixture')
    tools=tmp_path/'tools';(tools/'dist/src').mkdir(parents=True);(tools/'dist/src/server.js').write_text('//fixture')
    out=tmp_path/'release'
    builder().package(wheels,licenses,out,node=node,toolserver=tools)
    with zipfile.ZipFile(out/'payload.zip') as archive:
        manifest=json.loads(archive.read('setup-context.json'))
        assert manifest['components']==['node','tools']
        from odessy.setup.package_context import validate_components
        assert validate_components(manifest, 'execution', runtime=False)['copy'] == {'node','tools','link'}
        assert not any(n.startswith(('desktop/','harness/','monitor/')) for n in archive.namelist())
    assert 'payload=payload' in (out/'install-executor.py').read_text()
    assert 'verify' in (out/'run-executor.py').read_text()
    assert 'unencrypted' in (out/'README.md').read_text(encoding='utf-8')
    assert 'unzip payload.zip -d payload' in (out/'README.md').read_text(encoding='utf-8')
    with pytest.raises(FileExistsError):
        builder().package(wheels,licenses,out,node=node,toolserver=tools)


def test_missing_executor_fails_before_artifact(tmp_path):
    wheels=tmp_path/'wheels';wheels.mkdir()
    with zipfile.ZipFile(wheels/'odessy-1.0-py3-none-any.whl','w') as archive:
        archive.writestr('placeholder','')
    with pytest.raises(ValueError, match='lacks executor'):
        builder().package(wheels,tmp_path/'licenses',tmp_path/'out',node=tmp_path,toolserver=tmp_path)
    assert not (tmp_path/'out').exists()


def test_executor_launcher_uses_verified_installation_as_working_directory(tmp_path, monkeypatch):
    import subprocess
    version = 'a' * 32
    root = tmp_path / 'installation'; base = root / 'versions' / version
    (base / 'node').mkdir(parents=True); (base / 'node' / 'bin').mkdir()
    (base / 'node' / 'node.exe').write_bytes(b'fixture')
    (base / 'node' / 'bin' / 'node').write_bytes(b'fixture')
    (base / 'tools' / 'dist' / 'src').mkdir(parents=True)
    (base / 'tools' / 'dist' / 'src' / 'server.js').write_text('//fixture')
    (root / 'installation.json').write_text(json.dumps({'active': version}))
    captured = []
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: type('Result', (), {'stdout': '{"valid":true}'})())
    monkeypatch.setattr(subprocess, 'call', lambda *a, **k: captured.append((a, k)) or 0)
    monkeypatch.setattr(sys, 'argv', ['run-executor.py', '--installation', str(root), '--workspace', 'workspace'])
    with pytest.raises(SystemExit) as result: exec(builder().LAUNCHER, {'__name__': '__main__'})
    assert result.value.code == 0
    assert Path(captured[0][1]['cwd']).resolve() == base.resolve()
    assert Path(captured[0][0][0][-1]).is_absolute()
