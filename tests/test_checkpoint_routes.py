"""Frozen checkpoint routes over the real app shell (auth + registrar)."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from odessy.application.agent_basics import AgentBasics
from odessy.application.checkpoints.routes import register
from odessy.application.checkpoints.service import CheckpointService
from odessy.authority.policy import OdessyPolicy


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                          text=True, timeout=60, check=True).stdout


def has_git() -> bool:
    try:
        subprocess.run(['git', '--version'], capture_output=True, timeout=20, check=True)
        return True
    except Exception:
        return False


needs_git = pytest.mark.skipif(not has_git(), reason='git is not installed')


class Binding:
    def __init__(self, root: Path) -> None:
        self.service_instance_id = 'svc-1'
        self.session_id = 's1'
        self.workspace_id = 'ws-1'
        self.label = 'fixture'
        self.local = True
        self.policy = OdessyPolicy(root)
        self.root_path = root
        info = root.stat()
        self.root_identity = (info.st_dev, info.st_ino)

    def identity(self):
        return (self.service_instance_id, self.session_id, self.workspace_id,
                str(self.root_path), self.root_identity, repr(self.policy.identity()))

    def is_authorized(self) -> bool:
        return True


class Inspector:
    def __init__(self, binding: Binding) -> None:
        self.binding_value = binding

    def binding(self, sid):
        return self.binding_value


def harness(tmp_path: Path):
    pytest.importorskip('fastapi')
    httpx = pytest.importorskip('httpx')
    from odessy.application.api import create_app

    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'readme.txt').write_text('Before\n', encoding='utf-8')
    subprocess.run(['git', 'init', '-q', str(root)], check=True, timeout=60)
    git(root, 'config', 'user.email', 'fixture@example.test')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'add', '--', 'readme.txt')
    git(root, 'commit', '-q', '-m', 'base')

    holder: dict = {}

    def registrar(app, dependencies):
        service = CheckpointService(
            inspector=Inspector(Binding(root)),
            request_grant=lambda sid, plan: plan.get('action') != 'checkpoint.restore'
            or plan.get('preview_hash') != 'refused',
            state_dir=tmp_path / 'state')
        holder['service'] = service
        register(app, dependencies, service=service)

    app = create_app(lambda: (_ for _ in ()).throw(AssertionError('no engine')),
                     token='fixture-token', agent_basics=AgentBasics((registrar,)))
    app.state.sessions['s1'] = object()
    return app, root, holder, httpx


def call(app, httpx, method, path, *, token='fixture-token', body=None):
    async def run():
        headers = {} if token is None else {'Authorization': f'Bearer {token}'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test', headers=headers) as client:
            return await client.request(method, path, json=body)

    return asyncio.run(run())


@needs_git
def test_checkpoint_routes_require_auth_and_report_shapes(tmp_path):
    app, root, _, httpx = harness(tmp_path)
    assert call(app, httpx, 'GET', '/v1/sessions/s1/checkpoints',
                token=None).status_code == 401

    empty = call(app, httpx, 'GET', '/v1/sessions/s1/checkpoints')
    assert empty.status_code == 200
    assert empty.json() == {'version': 1, 'revision': 0, 'workspace_id': 'ws-1',
                            'checkpoints': []}

    created = call(app, httpx, 'POST', '/v1/sessions/s1/checkpoints',
                   body={'label': 'before edit'})
    assert created.status_code == 201
    checkpoint = created.json()
    assert checkpoint['label'] == 'before edit' and checkpoint['revision'] == 1
    assert checkpoint['version'] == 1 and set(checkpoint) >= {
        'id', 'label', 'created_at', 'revision', 'workspace_id'}

    preview = call(app, httpx, 'GET',
                   f'/v1/sessions/s1/checkpoints/{checkpoint["id"]}/preview')
    assert preview.status_code == 200
    assert preview.json()['expected_revision'] == 1
    assert preview.json()['changes'] == [
        {'path': 'readme.txt', 'status': 'unchanged'}]

    (root / 'readme.txt').write_text('After\n', encoding='utf-8')
    drifted = call(app, httpx, 'GET',
                   f'/v1/sessions/s1/checkpoints/{checkpoint["id"]}/preview')
    assert drifted.json()['changes'][0]['status'] == 'modified'

    restored = call(app, httpx, 'POST',
                    f'/v1/sessions/s1/checkpoints/{checkpoint["id"]}/restore',
                    body={'preview_hash': drifted.json()['preview_hash'],
                          'expected_revision': 1})
    assert restored.status_code == 200
    assert restored.json()['status'] == 'restored'
    assert (root / 'readme.txt').read_text(encoding='utf-8') == 'Before\n'


@needs_git
def test_route_errors_are_typed_and_do_not_mutate(tmp_path):
    app, root, _, httpx = harness(tmp_path)
    created = call(app, httpx, 'POST', '/v1/sessions/s1/checkpoints',
                   body={'label': 'baseline'}).json()
    base = f'/v1/sessions/s1/checkpoints/{created["id"]}'

    assert call(app, httpx, 'POST', '/v1/sessions/s1/checkpoints',
                body={'label': 'x', 'extra': 1}).status_code == 400
    assert call(app, httpx, 'POST', '/v1/sessions/s1/checkpoints',
                body={}).status_code == 400
    assert call(app, httpx, 'POST', '/v1/sessions/s1/checkpoints',
                body={'label': ''}).status_code == 400
    assert call(app, httpx, 'GET',
                '/v1/sessions/s1/checkpoints/ckpt_missing/preview').status_code == 404
    assert call(app, httpx, 'POST', f'{base}/restore',
                body={'preview_hash': '0' * 64}).status_code == 400
    assert call(app, httpx, 'POST', f'{base}/restore',
                body={'preview_hash': '0' * 64, 'expected_revision': 1}).status_code == 409
    refused = call(app, httpx, 'GET', base + '/preview').json()
    assert refused['changes'][0]['status'] == 'unchanged'
    assert (root / 'readme.txt').read_text(encoding='utf-8') == 'Before\n'
