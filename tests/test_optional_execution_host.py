import asyncio
from types import SimpleNamespace

from odessy.tools import lan_executor


def test_active_lan_selection_keeps_prompt_and_tools_on_same_hosts(monkeypatch):
    from odessy.config.deployment import environment_prompt, model_tool_names
    monkeypatch.setenv('ODESSY_DEPLOYMENT', 'single')
    lan_executor.select(lan_executor.LanConnection('host', 'Laptop', 'http://127.0.0.1:9/mcp', '/remote', 'dummy'))
    try:
        prompt = environment_prompt()
        assert 'laptop.terminal.run runs commands' in prompt
        assert 'separate machine' in prompt
        assert 'laptop.terminal.run' in model_tool_names()
    finally:
        lan_executor.clear()


def test_invalid_legacy_pairing_does_not_break_runtime_prompt(monkeypatch):
    from odessy.config.deployment import environment_prompt, model_tool_names
    from odessy.tools import paired
    monkeypatch.setenv('ODESSY_PAIRED_HOST', 'missing')
    def invalid(*args):
        raise paired.PairedConfigError('inconsistent optional executor')
    monkeypatch.setattr(paired, 'validate_configuration', invalid)
    assert 'terminal.run' in model_tool_names()
    assert 'terminal.run runs commands on this machine' in environment_prompt()


def test_remote_discovery_never_runs_during_session_start_or_local_turn(tmp_path, monkeypatch):
    from odessy import cli
    from odessy.application.bootstrap import create_controller
    from odessy.runtime.types import ToolRequest, FinalResponse

    async def run():
        monkeypatch.setattr(cli, 'node_tool_server_dependencies_installed', lambda: False)
        monkeypatch.setenv('ODESSY_DEPLOYMENT', 'single')
        connection = lan_executor.LanConnection('offline', 'Offline host',
            'http://127.0.0.1:9/mcp', '/remote', 'test-secret')
        lan_executor.select(connection)
        contacts = []
        def forbidden(*args, **kwargs):
            contacts.append(True)
            raise AssertionError('Startup must not contact a previously selected execution host')
        monkeypatch.setattr(lan_executor.LanConnection, 'client', forbidden)
        class Model:
            def propose(self, text, **kwargs):
                return ToolRequest('time.now', {})
            def respond_to_observation(self, text, observation, **kwargs):
                return FinalResponse('local tools are available')
        controller = None
        try:
            controller = await asyncio.wait_for(create_controller(workspace=tmp_path, model=Model()), 3)
            turn = await controller.run_turn('What time is it?')
            assert turn.final_text == 'local tools are available'
            assert not contacts, 'An unreachable executor must not consume session-start deadlines'
        finally:
            if controller:
                await controller.close()
            lan_executor.clear()
    asyncio.run(run())


def test_connected_remote_preserves_local_node_server(tmp_path, monkeypatch):
    from odessy import cli
    started = []
    class Node:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self):
            started.append(True)
            return 'http://127.0.0.1:12345/mcp'
        async def __aexit__(self, *args): pass
    async def run():
        monkeypatch.setenv('ODESSY_DEPLOYMENT', 'single')
        monkeypatch.setattr(cli, 'node_tool_server_dependencies_installed', lambda: True)
        monkeypatch.setattr(cli, 'NodeToolServerHttp', Node)
        connection = lan_executor.LanConnection('remote', 'Host', 'http://127.0.0.1:9/mcp', '/remote', 'test-secret',
            tools=(SimpleNamespace(name='filesystem.read'),))
        lan_executor.select(connection)
        try:
            async with cli._builtin_tool_servers(tmp_path) as servers:
                assert started, 'Selecting a remote host must not replace the local execution backend'
                assert any(getattr(target.server, 'server', None) == 'http://127.0.0.1:12345/mcp' for target in servers)
                assert any(isinstance(target.server, lan_executor.LanServer) for target in servers)
                from odessy.runtime.agent_loop import OdessyAgent
                agent = OdessyAgent.__new__(OdessyAgent)
                agent._mcp_servers = tuple(target.server for target in servers)
                registry = await agent._collect_tool_registry()
                assert isinstance(registry['filesystem.read'][1], lan_executor.LanServer), 'Remote file operations must not silently become local'
                assert registry['terminal.run'][1].local is True
        finally:
            lan_executor.clear()
    asyncio.run(run())
