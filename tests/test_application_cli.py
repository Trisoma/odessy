import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from odessy import cli
from odessy.runtime.types import AgentTurnResult


def test_text_uses_shared_controller_and_preserves_interactive_confirmation(monkeypatch, capsys):
    seen = []
    class Controller:
        profiles = {'current': {}}
        config = SimpleNamespace(participants={})
        async def run_turn(self, text, **kwargs):
            seen.append(text)
            return AgentTurnResult('shared answer', [])
    @asynccontextmanager
    async def runtime(**kwargs):
        seen.append(kwargs)
        yield Controller()
        seen.append('closed')
    monkeypatch.setattr(cli, 'controller_runtime', runtime, raising=False)
    monkeypatch.setattr(cli, '_build_model', lambda *a, **kw: object())
    @asynccontextmanager
    async def old_servers(*args, **kwargs):
        raise AssertionError('CLI bypassed shared session')
        yield ()
    monkeypatch.setattr(cli, '_tool_servers', old_servers)
    asyncio.run(cli._text_async('question', True))
    assert seen[0]['interactive'] is True
    assert seen[1:] == ['question', 'closed']
    assert 'shared answer' in capsys.readouterr().out


def test_single_voice_uses_controller_agent(monkeypatch):
    seen = []
    @asynccontextmanager
    async def runtime(**kwargs):
        seen.append(kwargs)
        yield object()
    async def voice_turn(agent, config):
        from odessy.application.bootstrap import ControllerAgent
        assert isinstance(agent, ControllerAgent)
        seen.append('voice')
    monkeypatch.setattr(cli, 'controller_runtime', runtime, raising=False)
    monkeypatch.setattr(cli, '_run_voice_turn', voice_turn)
    monkeypatch.setattr(cli, '_build_model', lambda *a, **kw: object())
    monkeypatch.setattr(cli.VoiceConfig, 'from_env', lambda: SimpleNamespace(ollama_model='fake'))
    @asynccontextmanager
    async def old_servers(*args, **kwargs):
        raise AssertionError('Voice bypassed shared session')
        yield ()
    monkeypatch.setattr(cli, '_tool_servers', old_servers)
    asyncio.run(cli._voice_async(True))
    assert seen[0]['interactive'] is True
    assert seen[1] == 'voice'


def test_text_turn_stop_exits_with_a_result_without_cancellation_traceback(monkeypatch, capsys):
    class Controller:
        profiles = {'current': {}}
        config = SimpleNamespace(participants={})
        async def run_turn(self, text, **kwargs):
            asyncio.current_task().cancel()
            await asyncio.sleep(0)
    @asynccontextmanager
    async def runtime(**kwargs):
        yield Controller()
    monkeypatch.setattr(cli, 'controller_runtime', runtime)
    monkeypatch.setattr(cli, '_build_model', lambda *args, **kwargs: object())
    asyncio.run(cli._text_async('Help', True))
    assert 'Stopped.' in capsys.readouterr().out

def test_serve_rejects_network_binding_before_startup():
    from odessy.application.serve import validate_binding
    assert validate_binding('127.0.0.1', 8765) == ('127.0.0.1', 8765)
    assert validate_binding('::1', 8765) == ('::1', 8765)
    with pytest.raises(ValueError, match='loopback'):
        validate_binding('0.0.0.0', 8765)
    with pytest.raises(ValueError, match='port'):
        validate_binding('localhost', 0)


@pytest.mark.parametrize('source', ['explicit', 'environment'])
def test_serve_does_not_log_authentication_token(monkeypatch, capsys, source):
    from odessy.application import api, serve
    from odessy.collaboration import config
    import uvicorn

    token = 'private-server-test-token'
    captured = []
    monkeypatch.delenv('ODESSY_API_TOKEN', raising=False)
    if source == 'environment':
        monkeypatch.setenv('ODESSY_API_TOKEN', token)
    monkeypatch.setattr(config, 'load_config', lambda: object())
    monkeypatch.setattr(api, 'create_app', lambda *args, **kwargs: captured.append(kwargs['token']) or object())
    monkeypatch.setattr(uvicorn, 'Config', lambda *args, **kwargs: object())
    class Server:
        def __init__(self, config):
            pass
        async def serve(self):
            pass
    monkeypatch.setattr(uvicorn, 'Server', Server)
    asyncio.run(serve.run_server(token=token if source == 'explicit' else None))
    assert captured == [token]
    output = capsys.readouterr()
    assert token not in output.out + output.err


def test_serve_requires_a_token_before_starting(monkeypatch, capsys):
    from odessy.application.serve import run_server
    monkeypatch.delenv('ODESSY_API_TOKEN', raising=False)
    with pytest.raises(ValueError, match='ODESSY_API_TOKEN'):
        asyncio.run(run_server())
    assert not capsys.readouterr().err


def test_serve_cli_routes_explicit_options_without_loading_model(monkeypatch):
    from odessy.application import serve
    seen = []
    async def server(**kwargs):
        seen.append(kwargs)
    monkeypatch.setattr(serve, 'run_server', server)
    cli.main(['serve', '--host', '127.0.0.1', '--port', '8899', '--fake-model'])
    assert seen[0]['port'] == 8899
    assert seen[0]['use_fake_model'] is True


def test_coding_agent_server_cli_is_explicit_trusted_stdio_operator(monkeypatch):
    import sys
    seen = []
    async def worker(**kwargs):
        seen.append(kwargs)
    monkeypatch.setitem(sys.modules, 'odessy.collaboration.worker', SimpleNamespace(run_worker=worker))
    cli.main(['coding-agent-server'])
    assert seen == [{'trusted_stdio_operator': True}]
