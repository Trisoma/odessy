import json
import pytest
from odessy.setup.maintenance import ManagedInstallation, digest
from odessy.setup.contracts import SetupSelection
from test_setup_maintenance import bundle, fake_runner


def test_payload_components_owned_and_execution_excludes_desktop(tmp_path):
    source=bundle(tmp_path);payload=tmp_path/'payload';payload.mkdir()
    files={}
    for name in ('desktop/app.exe','node/node.exe','tools/main.js','link/odessy-link.exe'):
        path=payload/name;path.parent.mkdir(exist_ok=True);path.write_bytes(b'component');files[name]=digest(path)
    (payload/'setup-context.json').write_text(json.dumps({'schema_version':1,'components':['desktop','node','tools','link'],'files':files}))
    manager=ManagedInstallation(tmp_path/'installed',runner=fake_runner,payload=payload)
    selection=SetupSelection(role='execution');plan=manager.plan(source,selection)
    manager.install(source,selection,plan['plan_id'])
    owned=manager.record()['versions'][manager.record()['active']]
    assert 'node/node.exe' in owned and 'tools/main.js' in owned and 'link/odessy-link.exe' in owned
    assert not any(name.startswith('desktop/') for name in owned)
    assert not (manager.root/'versions'/manager.record()['active']/'runtime.env').exists()
    (payload/'node/node.exe').write_bytes(b'changed')
    with pytest.raises(ValueError,match='checksum'):manager.plan(source,selection)
