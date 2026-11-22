import asyncio
import base64
import hashlib
import sys
from types import SimpleNamespace

import pytest

from odessy.authority.policy import OdessyPolicy
from odessy.runtime.types import ToolRequest, FinalResponse

KEY = base64.b64encode(b'fixture-host-public-key').decode()
FP = 'SHA256:' + base64.b64encode(hashlib.sha256(b'fixture-host-public-key').digest()).decode().rstrip('=')


@pytest.fixture(autouse=True)
def ssh_fixture(monkeypatch):
    from odessy.tools.ssh_executor import clear
    monkeypatch.setattr('odessy.tools.ssh_executor.read_identity',
        lambda host, **kw: SimpleNamespace(fingerprints=(('ed25519', FP),)))
    clear()
    yield
    clear()


def connection():
    from odessy.tools.ssh_executor import SshExecutionConnection
    from odessy.ssh.identity import SshHost
    return SshExecutionConnection(SshHost('fixture', 'fixture', 'user'),
        'fixture', FP, public_keys=(('ssh-ed25519', KEY),))


def test_remote_command_always_requires_exact_approval_and_preserves_hard_denials(tmp_path):
    policy = OdessyPolicy(tmp_path)
    assert policy.evaluate(ToolRequest('ssh.run', {'command': 'hostname'})).action == 'confirm'
    assert policy.evaluate(ToolRequest('ssh.run', {'command': 'rm -rf /'})).action == 'deny'
    assert policy.evaluate(ToolRequest('ssh.run', {'command': 'cat ~/.ssh/id_rsa'})).action == 'deny'


def test_real_agent_approval_output_loop_and_disconnect_revocation(tmp_path,monkeypatch):
    from odessy.tools.ssh_executor import SshExecutionConnection, select, clear
    from odessy.ssh.identity import SshHost
    from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
    async def run():
        selected = connection()
        select(selected)
        server = selected.server()
        async def spawn(*args, **kwargs):
            assert args[-1].startswith('sh -c ')
            return await asyncio.create_subprocess_exec(sys.executable, '-c',
                "print('remote-fixture-host');print('remote-user')", **kwargs)
        server.spawn = spawn
        class Model:
            def propose(self, text):return ToolRequest('ssh.run', {'command':'hostname; whoami; pwd'})
            def respond_to_observation(self, text, observation):return FinalResponse(observation.result['stdout'])
        plans=[]
        async def approve(plan):
            plans.append(plan)
            return True
        agent=OdessyAgent(model=Model(),policy=OdessyPolicy(tmp_path),mcp_server=ExecutionTarget(server),confirm_action=approve)
        try:
            result=await agent.run_turn('Show the remote host identity')
            assert 'remote-fixture-host' in result.final_text
            assert len(plans)==1 and plans[0]['operation']=='ssh.run'
            assert plans[0]['target_identity']['fingerprint']==FP
            assert plans[0]['arguments']['command']=='hostname; whoami; pwd'
            clear()
            with pytest.raises(PermissionError):await server.call_tool('ssh.run', {'command':'hostname'})
        finally:clear()
    asyncio.run(run())


def test_production_tool_composition_and_required_isolation(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from odessy import cli
    from odessy.tools.ssh_executor import select
    @asynccontextmanager
    async def local(*args, **kwargs):
        yield ()
    monkeypatch.setattr(cli, '_local_tool_servers', local)
    monkeypatch.setenv('ODESSY_DEPLOYMENT', 'single')
    async def run():
        select(connection())
        async with cli._builtin_tool_servers(tmp_path) as servers:
            tools = await servers[0].server.list_tools()
            assert [tool.name for tool in tools] == ['ssh.run']
    asyncio.run(run())
    policy = OdessyPolicy(tmp_path, target_isolation={'ssh': 'required'})
    assert policy.evaluate(ToolRequest('ssh.run', {'command': 'hostname'})).action == 'deny'


def test_declined_remote_command_never_starts_process(tmp_path,monkeypatch):
    from odessy.tools.ssh_executor import SshExecutionConnection, select, clear
    from odessy.ssh.identity import SshHost
    from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
    async def run():
        connection=SshExecutionConnection(SshHost('fixture','fixture','user'),'fixture','SHA256:test')
        select(connection);server=connection.server()
        async def forbidden(*a,**kw):raise AssertionError('Declined command executed')
        server.spawn=forbidden
        class Model:
            def propose(self,text):return ToolRequest('ssh.run',{'command':'hostname'})
        async def decline(plan):return False
        agent=OdessyAgent(model=Model(),policy=OdessyPolicy(tmp_path),mcp_server=ExecutionTarget(server),confirm_action=decline)
        try:
            with pytest.raises(PermissionError):await agent.run_turn('remote identity')
        finally:clear()
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['ask', 'auto', 'full'])
def test_all_modes_keep_remote_approval(mode):
    from test_access_mode import service_for, set_mode, review
    service, _ = service_for()
    state = set_mode(service, mode)
    assert service.review(review(operation='ssh.run', risk='high',
        rule='ssh_exact_command', revision=state['revision'])).outcome == 'ask'


@pytest.mark.parametrize('code,output,status', [(0, 'remote-ok', 'ok'), (7, 'remote-failed', 'failed')])
def test_bounded_output_remote_failure_and_pinned_argv(code, output, status):
    from odessy.tools.ssh_executor import select
    async def run():
        selected = connection()
        select(selected)
        server = selected.server()
        async def spawn(*args, **kwargs):
            assert args[1:3] == ('-F', 'none')
            assert 'HostName=fixture' in args and 'User=user' in args
            assert 'ProxyCommand=none' in args and 'PermitLocalCommand=no' in args
            option = next(arg for arg in args if arg.startswith('UserKnownHostsFile='))
            from pathlib import Path
            assert KEY in Path(option.split('=', 1)[1].strip('"')).read_text()
            return await asyncio.create_subprocess_exec(sys.executable, '-c',
                f"import sys;print('{output}');sys.stderr.write('x'*50000);sys.exit({code})", **kwargs)
        server.spawn = spawn
        result = await server.call_tool('ssh.run', {'command': 'hostname'})
        data = result.structured_content
        assert data['status'] == status and data['exit_code'] == code
        assert output in data['stdout'] and len(data['stderr']) <= 32768
        assert data['truncated'] and data['origin'] == 'ssh'
        assert result.is_error == (code != 0)
        assert not selected.processes
    asyncio.run(run())


def test_timeout_cancellation_and_disconnect_cleanup():
    from odessy.tools.ssh_executor import select, clear
    async def run():
        selected = connection()
        select(selected)
        server = selected.server()
        processes = []
        async def spawn(*args, **kwargs):
            process = await asyncio.create_subprocess_exec(sys.executable, '-c',
                'import time;time.sleep(30)', **kwargs)
            processes.append(process)
            return process
        server.spawn = spawn
        result = await server.call_tool('ssh.run', {'command': 'sleep 30', 'timeout': 1})
        assert result.structured_content['status'] == 'timed_out'
        task = asyncio.create_task(server.call_tool('ssh.run', {'command': 'sleep 30'}))
        while len(processes) < 2:
            await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        task = asyncio.create_task(server.call_tool('ssh.run', {'command': 'sleep 30'}))
        while len(processes) < 3:
            await asyncio.sleep(.01)
        clear()
        with pytest.raises(PermissionError):
            await task
        assert all(process.returncode is not None for process in processes)
        assert not selected.processes
    asyncio.run(run())


def test_changed_key_and_target_during_approval_do_not_dispatch(tmp_path, monkeypatch):
    from odessy.tools.ssh_executor import select, clear
    from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
    async def run():
        selected = connection()
        select(selected)
        server = selected.server()
        async def forbidden(*a, **k):
            raise AssertionError('Stale approval dispatched')
        server.spawn = forbidden
        class Model:
            def propose(self, text):
                return ToolRequest('ssh.run', {'command': 'hostname'})
        async def approve(plan):
            clear()
            select(connection())
            return True
        agent = OdessyAgent(model=Model(), policy=OdessyPolicy(tmp_path),
            mcp_server=ExecutionTarget(server), confirm_action=approve)
        with pytest.raises(PermissionError):
            await agent.run_turn('remote identity')
        clear()
        select(selected)
        selected.revoked = False
        monkeypatch.setattr('odessy.tools.ssh_executor.read_identity',
            lambda *a, **k: SimpleNamespace(fingerprints=(('ed25519', 'SHA256:changed'),)))
        with pytest.raises(PermissionError, match='identity changed'):
            await server.call_tool('ssh.run', {'command': 'hostname'})
    asyncio.run(run())


def test_authenticated_http_session_approval_and_output(tmp_path, monkeypatch):
    import httpx
    from odessy.application.api import create_app
    from odessy.application.session import SessionController
    from odessy.monitoring.settings import MonitoringSettings
    from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
    from odessy.tools.ssh_executor import current, SshServer
    host = connection().host
    monkeypatch.setattr('odessy.application.ssh_connections.resolve_host', lambda *a, **kw: host)
    monkeypatch.setattr('odessy.ssh.identity.read_identity', lambda *a, **kw:
        SimpleNamespace(known=True, primary_fingerprint=FP, public_keys=(('ssh-ed25519', KEY),)))
    async def spawn(self, *args, **kwargs):
        return await asyncio.create_subprocess_exec(sys.executable, '-c', "print('fixture-remote-host')", **kwargs)
    monkeypatch.setattr(SshServer, 'spawn_fixture', spawn, raising=False)
    class Model:
        def propose(self, text):
            return ToolRequest('ssh.run', {'command': 'hostname; whoami; pwd'})
        def respond_to_observation(self, text, observation):
            return FinalResponse(observation.result['stdout'])
    def factory():
        selected = current()
        server = selected.server()
        server.spawn = lambda *a, **kw: spawn(server, *a, **kw)
        controller = SessionController(model_factory=lambda _: Model(), profiles={'local': {}},
            agent_factory=lambda model, **kw: OdessyAgent(model=model, policy=OdessyPolicy(tmp_path),
                mcp_server=ExecutionTarget(server), confirm_action=controller.confirm_action))
        return controller
    async def run():
        monitor = SimpleNamespace(settings=MonitoringSettings(ssh_alias='fixture', ssh_binary='ssh'))
        app = create_app(factory, token='fixture-token', monitoring=monitor,
            conversations_enabled=True, config_path=tmp_path / 'profile.env')
        manager = app.state.ssh_connections
        manager._login_verified = True
        manager._verified_identity = {'hostname': host.hostname, 'user': host.user,
            'port': host.port, 'known_fingerprints': [FP]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
            base_url='http://test') as client:
            assert (await client.post('/v1/ssh/execution', json={'confirmed': True})).status_code == 401
            headers = {'Authorization': 'Bearer fixture-token'}
            for value in (False, 1, 'true'):
                assert (await client.post('/v1/ssh/execution', headers=headers,
                    json={'confirmed': value})).status_code == 400
            enabled = await client.post('/v1/ssh/execution', headers=headers, json={'confirmed': True})
            assert enabled.status_code == 200, enabled.text
            sid = (await client.post('/v1/sessions', headers=headers)).json()['id']
            route = '/v1/sessions/' + sid
            assert (await client.post(route + '/turns', headers=headers, json={'text': 'remote identity'})).status_code == 202
            controller = app.state.sessions[sid]
            for _ in range(150):
                if controller.pending_approvals:
                    break
                await asyncio.sleep(.02)
            plan = controller.pending_approvals[0]
            assert 'ssh.run' in str(plan) and FP in str(plan)
            reply = await client.post(route + '/approvals/' + plan['id'],
                headers=headers, json={'approved': True})
            assert reply.status_code == 200, reply.text
            await asyncio.gather(*app.state.turn_tasks.values())
            state = (await client.get(route, headers=headers)).json()
            assert 'fixture-remote-host' in str(state['history'])
            events = app.state.conversations.store.raw_events(controller._conversation_id)
            assert any(event['kind'] == 'tool_result' and
                event['payload'].get('output', {}).get('tool_name') == 'ssh.run' for event in events)
            assert (await client.delete('/v1/ssh/execution', headers=headers)).json()['execution'] is None
            await client.delete(route, headers=headers)
        manager.close()
        app.state.conversations.store.close()
    asyncio.run(run())


def test_password_remains_redacted_when_lease_clears_during_output(monkeypatch):
    from odessy.tools.ssh_executor import select
    async def run():
        selected = connection()
        secret = 'fixture-unlabelled-credential'
        selected.lease = SimpleNamespace(active=True, password=secret, environment=lambda helper: {})
        select(selected); server = selected.server()
        async def spawn(*args, **kwargs):
            selected.lease.password = ''
            return await asyncio.create_subprocess_exec(sys.executable, '-c',
                'print(' + repr(secret) + ')', stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
        server.spawn = spawn
        result = await server.call_tool('ssh.run', {'command': 'hostname'})
        assert secret not in result.structured_content['stdout']
        assert '<redacted>' in result.structured_content['stdout']
    asyncio.run(run())


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_password_crossing_output_cap_and_literal_command_are_safe(stream):
    from odessy.tools.ssh_executor import select
    async def run():
        selected = connection()
        secret = 'mango7'
        selected.lease = SimpleNamespace(active=True, password=secret, environment=lambda helper: {})
        select(selected)
        server = selected.server()
        command = 'printf %s BatchMode=yes'
        async def spawn(*args, **kwargs):
            assert args[-1].endswith(command + "'")
            assert 'BatchMode=no' in args
            code = "import sys;sys." + stream + ".write('x' * 32763 + 'mango7' + 'z' * 100)"
            return await asyncio.create_subprocess_exec(sys.executable, '-c', code, **kwargs)
        server.spawn = spawn
        result = (await server.call_tool('ssh.run', {'command': command})).structured_content
        assert 'mango' not in result[stream]
        assert result['truncated']
        assert len(result[stream].encode()) <= 32768
    asyncio.run(run())
