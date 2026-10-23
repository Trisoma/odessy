import pytest
from mcp.server import MCPServer
from odessy.authority.policy import OdessyPolicy
from odessy.runtime.agent_loop import OdessyAgent, ExecutionTarget
from odessy.runtime.types import ToolRequest, FinalResponse


@pytest.mark.anyio
async def test_combined_request_keeps_ordinary_and_escalation_gates(tmp_path):
    requests, executed = [], []
    server = MCPServer('combined-test')
    @server.tool(name='terminal.run')
    async def run(command: str, mode: str = '', isolation: str = '') -> dict[str, object]:
        executed.append((command, mode, isolation))
        return {'status': 'ok'}
    class Model:
        def propose(self, text):
            return ToolRequest('terminal.run', {'command': 'mkdir build', 'sandbox_permissions': 'danger-full-access', 'justification': 'just reading'})
        def respond_to_observation(self, text, observation):
            return FinalResponse('Done')
    async def confirm(details):
        requests.append(details)
        return True
    agent = OdessyAgent(model=Model(), policy=OdessyPolicy(tmp_path), mcp_server=ExecutionTarget(server), confirm_action=confirm)
    result = await agent.run_turn('Build')
    assert len(requests) == 1
    assert requests[0]['scopes'] == ['authorization', 'escalation']
    assert requests[0]['allowed_channels'] == ['terminal', 'authenticated_ui']
    assert executed == [('mkdir build', 'danger-full-access', 'preferred')]
    assert any(item.event == 'policy_confirmed' for item in result.log)
    assert any(item.event == 'execution_mode_resolved' for item in result.log)


@pytest.mark.anyio
async def test_changed_arguments_invalidate_approval_before_dispatch(tmp_path):
    server = MCPServer('mutation-test')
    executed = []
    @server.tool(name='terminal.run')
    async def run(command: str, mode: str = '', isolation: str = '') -> dict[str, object]:
        executed.append(command)
        return {'status': 'ok'}
    action = ToolRequest('terminal.run', {'command': 'mkdir build'})
    class Model:
        def propose(self, text): return action
    async def confirm(details):
        action.arguments['command'] = 'mkdir elsewhere'
        return True
    agent = OdessyAgent(model=Model(), policy=OdessyPolicy(tmp_path), mcp_server=ExecutionTarget(server), confirm_action=confirm)
    with pytest.raises(PermissionError, match='changed'):
        await agent.run_turn('Build')
    assert not executed


@pytest.mark.anyio
async def test_stop_during_transport_setup_prevents_dispatch(tmp_path, monkeypatch):
    import asyncio
    import odessy.runtime.agent_loop as runtime
    server = MCPServer('stop-during-connection')
    executed, connections = [], []
    stop = asyncio.Event()
    @server.tool(name='time.now')
    async def now() -> dict[str, object]:
        executed.append(True)
        return {'time': 'now'}
    original = runtime.Client
    class Client:
        def __init__(self, target): self.inner = original(target)
        async def __aenter__(self):
            value = await self.inner.__aenter__()
            connections.append(True)
            if len(connections) == 2:
                stop.set()
            return value
        async def __aexit__(self, *args):
            return await self.inner.__aexit__(*args)
    monkeypatch.setattr(runtime, 'Client', Client)
    class Model:
        def propose(self, text): return ToolRequest('time.now')
    agent = OdessyAgent(model=Model(), policy=OdessyPolicy(tmp_path),
        mcp_server=ExecutionTarget(server), confirm_action=lambda details: False)
    with pytest.raises(PermissionError, match='stopped'):
        await agent.run_turn('Time?', stop_event=stop)
    assert not executed


@pytest.mark.anyio
async def test_external_grant_rechecks_after_session_opens():
    import asyncio
    from contextlib import asynccontextmanager
    from odessy.tools.external_tools import NamespacedServer, ServerConfig
    executed = []
    class Client:
        async def call_tool(self, name, arguments):
            executed.append(name)
    class Server(NamespacedServer):
        @asynccontextmanager
        async def session(self):
            await asyncio.sleep(0)
            yield Client()
    server = Server(ServerConfig(name='test', transport='http', url='http://127.0.0.1:9/mcp'))
    def stale_grant():
        raise PermissionError('Policy changed')
    with pytest.raises(PermissionError, match='changed'):
        await server.call_tool('write', {}, before_dispatch=stale_grant)
    assert not executed
