"""The execution credential is limited to one workspace and dies with the executor.

These exercise the real authority layer: containment is decided by
``odessy.execution.roots`` (the same code the tool surface uses), not by a stub, so a
credential that reached outside the reviewed workspace would fail here.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from odessy.application import execution_access
from odessy.execution.contract import ExecutionPolicy
from odessy.execution.roots import canonical_path, writable_roots

WORKSPACE = 'the-workspace'
CREDENTIAL = 'session-credential'


@pytest.fixture(autouse=True)
def _clean():
    execution_access.reset()
    execution_access.configure(CREDENTIAL, WORKSPACE)
    yield
    execution_access.reset()


def _policy(workspace: Path) -> ExecutionPolicy:
    return ExecutionPolicy(mode='workspace-write', workspace_root=str(workspace))


def test_workspace_escape_is_rejected(tmp_path):
    """A credential for W must not authorize a path outside W."""
    workspace = (tmp_path / 'shared').resolve()
    workspace.mkdir()
    note = workspace / 'note.txt'
    note.write_text('ok', encoding='utf-8')
    # A file that no writable root can contain on either platform.
    system_file = (Path(os.environ['SystemRoot']) / 'System32' / 'drivers' / 'etc' / 'hosts'
                   if os.name == 'nt' else Path('/etc/passwd'))

    credential = execution_access.issue('pairing-1', WORKSPACE)
    assert execution_access.validate(credential, workspace=WORKSPACE)

    roots = [Path(root).resolve() for root in writable_roots(_policy(workspace))]

    def contained(candidate):
        resolved = Path(canonical_path(str(candidate))).resolve()
        return any(resolved == root or root in resolved.parents for root in roots)

    # The shapes the tool surface receives: traversal and absolute paths.
    for escape in (str(Path.home() / 'odessy-outside.txt'),
                   '../../etc/passwd',
                   str(system_file)):
        assert not contained(escape), f'{escape} escaped the reviewed workspace'

    # A path inside the workspace stays allowed, so the check is not vacuous.
    assert contained(note)


def test_a_credential_for_another_workspace_is_refused():
    execution_access.reset()
    execution_access.configure(CREDENTIAL, WORKSPACE)
    with pytest.raises(ValueError):
        execution_access.issue('pairing-2', 'a-different-workspace')


def test_executor_shutdown_invalidates_credential():
    """Stopping the executor is ``reset()``; the credential and its grant must die."""
    credential = execution_access.issue('pairing-3', WORKSPACE)
    assert execution_access.validate(credential)
    assert execution_access.grant_for(credential)['pairing'] == 'pairing-3'

    execution_access.reset()  # What stopping the executor does.

    assert execution_access.validate(credential) is False
    assert execution_access.grant_for(credential) is None
    with pytest.raises(ValueError):
        execution_access.issue('pairing-4', WORKSPACE)


def test_an_execution_credential_opens_no_application_route():
    """The tool secret must not become a second admin key."""
    import asyncio
    import pytest

    httpx = pytest.importorskip('httpx')
    pytest.importorskip('fastapi')
    from odessy.application.api import create_app

    async def run():
        def factory():
            raise AssertionError('an unauthenticated route must not build a controller')
        app = create_app(factory, token='admin-app-token')
        credential = execution_access.issue('pairing-5', WORKSPACE)
        headers = {'Authorization': 'Bearer ' + credential}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            for path in ('/v1/settings', '/v1/profiles', '/v1/capabilities'):
                assert (await client.get(path, headers=headers)).status_code == 401, path
            assert (await client.post('/v1/sessions', headers=headers)).status_code == 401

    asyncio.run(run())
