"""Opt-in real ConPTY gate, separate from ordinary offline capability tests."""
import asyncio
import os
from pathlib import Path

import pytest

from test_checkpoint_service import Binding, Inspector


@pytest.mark.skipif(not os.getenv('ODESSY_PTY_MODULE_ROOT'), reason='Pinned native PTY gate is opt-in')
def test_real_pty_input_resize_interrupt_close_revocation_and_session_isolation(tmp_path):
    from odessy.application.terminals.service import LocalTerminalService, TerminalError
    root = tmp_path / 'workspace'
    root.mkdir()
    binding = Binding(root)
    allowed = [True]
    async def grant(sid, plan):
        assert plan['scopes'] == ['authorization']
        return allowed[0]
    api = LocalTerminalService(
        inspector=Inspector(binding), request_grant=grant,
        node=Path('C:/Program Files/nodejs/node.exe'),
        module_root=Path(os.environ['ODESSY_PTY_MODULE_ROOT']),
        helper=Path(__file__).parents[1] / 'tools/src/agent_basics_terminal/helper.cjs')
    async def run():
        allowed[0] = False
        with pytest.raises(TerminalError) as denied:
            await api.create('s1')
        assert denied.value.status == 403 and not api.handles
        allowed[0] = True
        handle = await api.create('s1')
        tid = handle['id']
        with pytest.raises(TerminalError):
            await api.output('other-session', tid)
        resized = await api.resize('s1', tid, columns=100, rows=35, expected_revision=0)
        await api.input('s1', tid, text='set ODESSY_MARKER=NATIVE_4182\recho ODESSY_%ODESSY_MARKER%\r',
                        expected_revision=resized['revision'])
        for _ in range(80):
            output = await api.output('s1', tid)
            if 'ODESSY_NATIVE_4182' in output['output']:
                break
            await asyncio.sleep(.05)
        assert 'ODESSY_NATIVE_4182' in output['output']
        interrupted = await api.interrupt('s1', tid, expected_revision=1)
        closed = await api.close('s1', tid, expected_revision=interrupted['revision'])
        assert closed['status'] == 'exited'
        assert api.handles[tid]['process'].returncode is not None
        fresh = await api.create('s1')
        binding.authorized = False
        await asyncio.sleep(.5)  # Revocation stops an idle terminal without another request.
        assert api.handles[fresh['id']]['process'].returncode is not None
        with pytest.raises(TerminalError):
            await api.output('s1', fresh['id'])
        await api.close_session('s1')
        assert api.handles[fresh['id']]['process'].returncode is not None
    asyncio.run(run())


@pytest.mark.skipif(not os.getenv('ODESSY_PTY_MODULE_ROOT'), reason='Pinned native PTY gate is opt-in')
def test_real_terminal_authenticated_http_and_bounded_output(tmp_path):
    from odessy.application.api import create_app
    from odessy.application.terminals.routes import register_local
    from odessy.application.terminals.service import LocalTerminalService
    import httpx
    root = tmp_path / 'workspace'
    root.mkdir()
    api = LocalTerminalService(inspector=Inspector(Binding(root)),
        request_grant=lambda sid, plan: True, node=Path('C:/Program Files/nodejs/node.exe'),
        module_root=Path(os.environ['ODESSY_PTY_MODULE_ROOT']),
        helper=Path(__file__).parents[1] / 'tools/src/agent_basics_terminal/helper.cjs')
    def registrar(app, deps):
        register_local(app, deps, service=api)
    from odessy.application.agent_basics import AgentBasics
    app = create_app(lambda: (_ for _ in ()).throw(AssertionError('no model')),
                     token='native-fixture', agent_basics=AgentBasics((registrar,)))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            path = '/v1/sessions/s1/terminals'
            denied = await client.post(path, json={'columns': 80, 'rows': 24})
            assert denied.status_code == 401 and not api.handles
            client.headers['Authorization'] = 'Bearer native-fixture'
            response = await client.post(path, json={'columns': 80, 'rows': 24})
            assert response.status_code == 201, response.text
            tid = response.json()['id']
            try:
                refused = await client.put(f'{path}/{tid}/size', json={
                    'columns': 90, 'rows': 30, 'expected_revision': 99})
                assert refused.status_code == 409
                sent = await client.post(f'{path}/{tid}/input', json={
                    'text': 'for /l %i in (1,1,5000) do @echo ' + 'x' * 70 + '\r',
                    'expected_revision': 0})
                assert sent.status_code == 204
                for _ in range(100):
                    if api.handles[tid]['start'] > 0:
                        break
                    await asyncio.sleep(.05)
                result = await client.get(f'{path}/{tid}/output?after=0')
                assert result.status_code == 200
                assert result.json()['truncated'] is True
                assert len(result.json()['output'].encode('utf-8')) <= 65536
                assert len(api.handles[tid]['buffer']) <= 200 * 1024
            finally:
                await api.close_session('s1')
            assert api.handles[tid]['process'].returncode is not None
    asyncio.run(run())
