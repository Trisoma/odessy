"""A folder whose owned files are gone must accept a new installation."""
from __future__ import annotations
import json

from odessy.setup.maintenance import ManagedInstallation, digest
from odessy.setup.contracts import SetupSelection
from odessy.setup.package_context import context
from test_setup_maintenance import bundle, fake_runner


def _payload(tmp_path):
    payload = tmp_path / 'payload'
    payload.mkdir()
    files = {}
    for name in ('desktop/app.exe', 'wheelhouse/manifest.json'):
        path = payload / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'component')
        files[name] = digest(path)
    (payload / 'setup-context.json').write_text(json.dumps(
        {'schema_version': 1, 'components': ['desktop'], 'files': files}))
    return payload


def test_an_uninstalled_folder_is_offered_as_a_new_installation(tmp_path):
    source = bundle(tmp_path)
    payload = _payload(tmp_path)
    manager = ManagedInstallation(tmp_path / 'App', runner=fake_runner,
                                  payload=payload)
    selection = SetupSelection(role='combined')
    plan = manager.plan(source, selection)
    manager.install(source, selection, plan['plan_id'])
    assert context(payload=payload, root=manager.root)['installation_format'] == 'versioned'

    manager.uninstall(confirm=manager.identity())
    # The record survives so management can report what happened, but the folder
    # reads as installable instead of "Odessy is already installed here".
    assert manager.record()['active'] is None
    assert context(payload=payload, root=manager.root)['installation_format'] == 'new'

    replan = manager.plan(source, selection)
    manager.install(source, selection, replan['plan_id'])
    assert context(payload=payload, root=manager.root)['installation_format'] == 'versioned'
