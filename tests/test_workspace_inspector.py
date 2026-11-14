"""Real HTTP/service/policy tests. No model, remote host or user workspace."""
import asyncio
from dataclasses import replace
import hashlib
import os
from types import SimpleNamespace

import pytest

from odessy.application.workspace_inspector import (
    InspectorError, WorkspaceBinding, WorkspaceInspector, ReviewedWrite,
    recorded_outputs, register, bind_write_review, ContentBoundApproval,
)
from odessy.authority.policy import OdessyPolicy
from odessy.runtime.types import AgentTurnResult, ToolObservation, ToolRequest


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv('ODESSY_HOOKS_FILE', str(tmp_path / 'no-hooks.toml'))
    monkeypatch.delenv('ODESSY_HOOKS_REQUIRED', raising=False)
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'readme.txt').write_bytes(b'Hello Odessy\n')
    (root / '.env').write_text('private fixture', encoding='utf-8')
    state = SimpleNamespace(allowed=True)
    state.binding = WorkspaceBinding('service', 'sid', 'workspace-1', 'Project',
                                     OdessyPolicy(root), lambda: state.allowed, local=True)
    def resolve(sid):
        if sid != 'sid':
            raise InspectorError(404, 'Session not found')
        return state.binding
    state.inspector = WorkspaceInspector(resolve)
    state.root = root
    return state


def test_http_real_reads_bounds_errors_and_no_execution(owner):
    async def run():
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
        import httpx
        app = FastAPI()
        @app.middleware('http')
        async def auth(request: Request, call_next):
            if request.headers.get('Authorization') != 'Bearer isolated-fixture':
                return JSONResponse(status_code=401, content={'detail': 'Unauthorized'})
            return await call_next(request)
        register(app, owner.inspector)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            base = '/v1/sessions/sid/workspace'
            assert (await client.get(base)).status_code == 401
            client.headers['Authorization'] = 'Bearer isolated-fixture'
            desc = (await client.get(base)).json()
            assert desc['capabilities'] == {'read_files': True, 'proposed_changes': False, 'recorded_output': True}
            tree = (await client.get(base + '/tree')).json()
            assert tree['entries'] == [{'name': 'readme.txt', 'path': 'readme.txt', 'kind': 'file'}]
            file = (await client.get(base + '/file', params={'path': 'readme.txt'})).json()
            assert file['sha256'] == hashlib.sha256(b'Hello Odessy\n').hexdigest()
            assert file['text'] == 'Hello Odessy\n'
            assert file['service_instance_id'] == tree['service_instance_id'] == 'service'
            for path, code in [('../outside', 403), ('..\\outside', 403), ('.env', 403),
                               ('C:\\Windows\\x', 400), ('/etc/passwd', 400), ('missing.txt', 404)]:
                response = await client.get(base + '/file', params={'path': path})
                assert response.status_code == code, response.text
                assert 'private fixture' not in response.text
            (owner.root / 'binary').write_bytes(b'abc\0def')
            (owner.root / 'invalid').write_bytes(b'\xff')
            (owner.root / 'large').write_bytes(b'x' * (256 * 1024 + 1))
            for path, code in [('binary', 415), ('invalid', 415), ('large', 413), ('.', 415)]:
                assert (await client.get(base + '/file', params={'path': path})).status_code == code
            owner.allowed = False
            assert (await client.get(base + '/file', params={'path': 'readme.txt'})).status_code == 403
            assert (await client.get('/v1/sessions/foreign/workspace')).status_code == 404
            assert (await client.post(base + '/apply', json={})).status_code in (404, 405)
    asyncio.run(run())


def test_symlink_and_windows_junction_escape(owner, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'readme.txt').write_text('outside fixture')
    link = owner.root / 'alias'
    junction = False
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != 'nt':
            pytest.skip('Symlinks unavailable')
        # Native Windows junction requires no Developer Mode/admin privileges.
        import subprocess
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
        if result.returncode:
            pytest.skip('Junction creation unavailable')
        junction = True
    try:
        async def run():
            with pytest.raises(InspectorError) as failure:
                await owner.inspector.file('sid', 'alias/readme.txt')
            assert failure.value.status == 403
            tree = await owner.inspector.tree('sid')
            assert not any(e['name'] == 'alias' for e in tree['entries'])
        asyncio.run(run())
    finally:
        if junction:
            os.rmdir(link)  # Remove the junction only, never its target.
        else:
            link.unlink()


def test_hooks_and_revocation_during_await_are_not_bypassed(owner, monkeypatch):
    from odessy.application import workspace_inspector as module
    from odessy.runtime.types import PolicyDecision
    async def run():
        async def require_confirmation(hooks, request, decision):
            return PolicyDecision('confirm', 'fixture', 'low')
        monkeypatch.setattr(module, 'screen_request', require_confirmation)
        with pytest.raises(InspectorError) as failure:
            await owner.inspector.file('sid', 'readme.txt')
        assert failure.value.status == 403
        async def revoke(hooks, request, decision):
            owner.allowed = False
            return decision
        monkeypatch.setattr(module, 'screen_request', revoke)
        with pytest.raises(InspectorError) as failure:
            await owner.inspector.file('sid', 'readme.txt')
        assert failure.value.status == 403
    asyncio.run(run())


def test_os_read_denial_and_replaced_root_are_refused(owner, monkeypatch):
    from odessy.application import workspace_inspector as module
    original = module.os.open
    def denied(*args, **kwargs):
        raise PermissionError('private external path must not appear in response')
    async def run():
        monkeypatch.setattr(module.os, 'open', denied)
        with pytest.raises(InspectorError) as failure:
            await owner.inspector.file('sid', 'readme.txt')
        assert failure.value.status == 403 and 'private external' not in str(failure.value)
        monkeypatch.setattr(module.os, 'open', original)
        old = owner.root.with_name('moved-workspace')
        owner.root.rename(old)
        owner.root.mkdir()
        (owner.root / 'readme.txt').write_text('replacement')
        with pytest.raises(InspectorError) as failure:
            await owner.inspector.file('sid', 'readme.txt')
        assert failure.value.status == 409
    asyncio.run(run())


def test_tree_cap_local_only_and_changed_binding(owner):
    async def run():
        for index in range(205):
            (owner.root / f'f{index}.txt').write_text('x')
        tree = await owner.inspector.tree('sid')
        assert len(tree['entries']) <= 200 and tree['truncated'] is True
        original = owner.binding
        owner.binding = replace(original, local=False)
        with pytest.raises(InspectorError):
            owner.inspector.descriptor('sid')
        owner.binding = replace(original, workspace_id='changed')
        with pytest.raises(InspectorError) as failure:
            owner.inspector.binding('sid', original)
        assert failure.value.status == 409
    asyncio.run(run())


def test_exact_write_guard_stale_content_request_and_context(owner):
    async def run():
        request = ToolRequest('filesystem.write', {'path': './readme.txt', 'content': 'after\n'})
        guard = await ReviewedWrite.capture(owner.inspector, 'sid', 'approval-1', request)
        assert guard.public()['before_text'] == 'Hello Odessy\n'
        assert guard.public()['path'] == 'readme.txt'
        guard.validate_dispatch(owner.inspector, request)
        await guard.validate(owner.inspector, 'sid', 'approval-1', request)
        for sid, rid, req in [('sid', 'foreign', request), ('sid', 'approval-1',
                ToolRequest('filesystem.write', {'path': 'readme.txt', 'content': 'changed'})),
                ('foreign', 'approval-1', request)]:
            with pytest.raises(InspectorError):
                await guard.validate(owner.inspector, sid, rid, req)
        (owner.root / 'readme.txt').write_text('concurrent edit')
        with pytest.raises(InspectorError) as failure:
            await guard.validate(owner.inspector, 'sid', 'approval-1', request)
        assert failure.value.status == 409
        assert (owner.root / 'readme.txt').read_text() == 'concurrent edit'  # Guard never writes.
    asyncio.run(run())


def test_public_result_projection_real_status_bounds_order_and_harness_absence():
    result = AgentTurnResult('done', [], tool_observations=[
        ToolObservation(ToolRequest('terminal.run'), {'stdout': 'x' * 70000, 'stderr': '',
            'exit_code': 0, 'status': 'ok', 'stdout_truncated': True}, 'call-a'),
        ToolObservation(ToolRequest('filesystem.read'), {'text': 'not terminal'}),
        ToolObservation(ToolRequest('terminal.run'), {'stdout': '', 'stderr': 'failed',
            'exit_code': 2, 'status': 'failed'}, 'call-b'),
        ToolObservation(ToolRequest('terminal.run'), {'status': 'cancelled'}, 'call-c')])
    outputs = recorded_outputs(result, session_id='sid', turn_id='turn', sanitize=lambda s: s.replace('failed', '[filtered]'))
    assert [o['call_id'] for o in outputs] == ['call-a', 'call-b', 'call-c']
    assert outputs[0]['truncated'] and len(outputs[0]['stdout']) == 65536
    assert outputs[1]['state'] == 'error' and outputs[1]['stderr'] == '[filtered]'
    assert outputs[2]['state'] == 'cancelled'
    assert recorded_outputs(AgentTurnResult('Harness answer', []), session_id='sid', turn_id='turn') == []


def test_content_adapter_uses_existing_broker_and_bound_dispatch_guard(owner):
    from odessy.authority.action_approval import build_action_approval
    from odessy.interaction.broker import InteractionBroker
    from odessy.runtime.agent_loop import _BoundArguments
    async def run():
        request = ToolRequest('filesystem.write', {'path': 'readme.txt', 'content': 'after'})
        policy = owner.binding.policy
        decision = policy.evaluate(request)
        plan = build_action_approval(request, decision, policy, call_id='call', execution_target='local')
        bound = await bind_write_review(plan, request, owner.inspector, 'sid')
        assert isinstance(bound, ContentBoundApproval)
        events = []
        broker = InteractionBroker(lambda kind, entry: events.append((kind, entry)), lambda value: value)
        broker.start_turn('turn')
        pending = asyncio.create_task(broker.request_approval(bound.public_dict()))
        await asyncio.sleep(0)
        displayed = broker.pending_approvals[0]
        assert displayed['proposed_change']['before_sha256'] == hashlib.sha256(b'Hello Odessy\n').hexdigest()
        assert displayed['id'] != 'call'
        broker.resolve_approval(displayed['id'], True, channel='authenticated_ui')
        assert await pending is True
        def validate():
            if not bound.matches(request, decision, policy, execution_target='local'):
                raise PermissionError('Content or authority changed')
        arguments = _BoundArguments(dict(request.arguments), validate)
        arguments.validate()
        (owner.root / 'readme.txt').write_text('concurrent edit after approval')
        with pytest.raises(PermissionError):
            arguments.validate()
        assert (owner.root / 'readme.txt').read_text() == 'concurrent edit after approval'
        broker.close()
    asyncio.run(run())
