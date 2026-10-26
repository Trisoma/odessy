"""Portable release ownership, relocation and configuration regressions."""
import hashlib
import json
from pathlib import Path
import pytest
from odessy.setup import portable


def bundle(tmp_path, monkeypatch):
    root = tmp_path / 'portable app'
    for name in ('runtime/bin/python3', 'desktop/odessy_desktop', 'node/bin/node',
                 'link/odessy-link', 'monitor/odessy-monitor', 'tools/dist/src/server.js'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'executable')
    files = {p.relative_to(root).as_posix(): {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'mode': 493}
             for p in root.rglob('*') if p.is_file()}
    (root / 'portable-manifest.json').write_text(json.dumps({'schema_version': 1, 'platform': 'linux',
        'architecture': 'x86_64', 'version': '0.5.0.dev0', 'files': files}))
    state = tmp_path / 'user-state'
    monkeypatch.setenv('ODESSY_PORTABLE_ROOT', str(root))
    monkeypatch.setenv('ODESSY_PORTABLE_STATE', str(state))
    return root, state


def test_portable_spec_is_unconfigured_and_state_is_external(tmp_path, monkeypatch):
    root, state = bundle(tmp_path, monkeypatch)
    spec = portable.spec(state)
    assert spec['portable'] is True and spec['model_configured'] is False
    assert spec['python'] == str(root / 'runtime/bin/python3')
    assert not (root / 'configuration.json').exists()
    assert not state.exists(), 'inspection must not mutate state'


def test_configure_binds_review_to_state_and_arguments(tmp_path, monkeypatch):
    root, state = bundle(tmp_path, monkeypatch)
    selection = {'role': 'combined', 'model': {'provider': 'ollama', 'name': 'existing:q8'}}
    request = {'action': 'package-configure-review', 'root': str(state),
               'selection': selection, 'workspace': str(tmp_path / 'work')}
    reviewed = portable.dispatch(request)
    with pytest.raises(ValueError, match='review'):
        portable.dispatch(dict(request, action='package-configure', confirm='wrong'))
    result = portable.dispatch(dict(request, action='package-configure', confirm=reviewed['plan_id']))
    assert result['configured']
    assert portable.spec(state)['model_configured']
    with pytest.raises(ValueError, match='review'):
        portable.dispatch(dict(request, action='package-configure', confirm=reviewed['plan_id']))
    assert not (root / 'configuration.json').exists()


def test_portable_relocation_and_tamper_detection(tmp_path, monkeypatch):
    root, state = bundle(tmp_path, monkeypatch)
    moved = root.with_name('moved bundle')
    root.rename(moved)
    monkeypatch.setenv('ODESSY_PORTABLE_ROOT', str(moved))
    assert portable.spec(state)['desktop'].startswith(str(moved))
    (moved / 'desktop/odessy_desktop').write_bytes(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        portable.verify()


def test_portable_refuses_installer_mutations_and_state_inside_bundle(tmp_path, monkeypatch):
    root, state = bundle(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='not supported'):
        portable.dispatch({'action': 'package-uninstall', 'root': str(state)})
    monkeypatch.setenv('ODESSY_PORTABLE_STATE', str(root / 'state'))
    with pytest.raises(ValueError, match='outside'):
        portable.spec(root / 'state')


def test_bridge_routes_portable_without_creating_install_record(tmp_path, monkeypatch):
    _, state = bundle(tmp_path, monkeypatch)
    from odessy.setup.native_bridge import manage
    result = manage({'action': 'installed-spec', 'root': str(state)})
    assert result['portable'] is True
    assert not (state / 'installation.json').exists()

def test_required_symlink_checks_target_and_directory_links_verify(tmp_path, monkeypatch):
    root,state=bundle(tmp_path,monkeypatch)
    binary=root/'runtime/bin/python3'
    target=binary.with_name('python3.real')
    binary.rename(target)
    try:binary.symlink_to(target.name)
    except OSError:pytest.skip('symlink privilege unavailable')
    directory=root/'runtime/lib';directory.mkdir()
    directory_link=root/'runtime/lib64';directory_link.symlink_to('lib',target_is_directory=True)
    data=json.loads((root/'portable-manifest.json').read_text())
    data['files']['runtime/bin/python3.real']=data['files']['runtime/bin/python3']
    data['files']['runtime/bin/python3']={'link':'python3.real'}
    data['files']['runtime/lib64']={'link':'lib'}
    (root/'portable-manifest.json').write_text(json.dumps(data))
    assert portable.verify()['valid']
    target.write_bytes(b'tampered runtime')
    with pytest.raises(ValueError,match='checksum'):portable.spec(state)
