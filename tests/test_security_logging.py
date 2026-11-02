import asyncio
import urllib.error
import urllib.request

import pytest


def test_model_debug_does_not_emit_opaque_payloads(monkeypatch, capsys):
    from odessy.models.ollama_model import _debug_model_repair, _debug_parsed_action
    from odessy.runtime.types import FinalResponse, ToolRequest
    monkeypatch.setenv('ODESSY_DEBUG_MODEL_REPAIR', '1')
    opaque = 'opaque-private-value-with-no-token-prefix'
    _debug_model_repair([('validation failure', opaque)])
    _debug_parsed_action(FinalResponse(opaque))
    _debug_parsed_action(ToolRequest('terminal.run', {'command': opaque}))
    output = capsys.readouterr().out
    assert opaque not in output
    assert 'validation failure' in output


def test_manual_server_requires_an_explicit_token_before_startup(monkeypatch):
    from odessy.application.serve import run_server
    monkeypatch.delenv('ODESSY_API_TOKEN', raising=False)
    with pytest.raises(ValueError, match='ODESSY_API_TOKEN'):
        asyncio.run(run_server())


def test_configured_token_is_not_logged(monkeypatch, capsys):
    uvicorn = pytest.importorskip('uvicorn')
    pytest.importorskip('fastapi')
    from odessy.application.serve import run_server
    class Server:
        def __init__(self, config):
            pass
        async def serve(self):
            pass
    monkeypatch.setattr(uvicorn, 'Server', Server)
    secret = 'opaque-server-authentication-fixture'
    asyncio.run(run_server(token=secret))
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


def test_node_http_error_does_not_expose_exception_or_html(tmp_path):
    from odessy.tools.node_mcp import NodeToolServerHttp, node_tool_server_dependencies_installed
    if not node_tool_server_dependencies_installed():
        pytest.skip('Node tool server dependencies required')
    async def run():
        async with NodeToolServerHttp(tmp_path, extra_env={'ODESSY_MCP_TOKEN': 'http-error-fixture'}) as url:
            def request():
                body = b'<script>private-invalid-json</script>'
                req = urllib.request.Request(url, data=body, headers={
                    'Authorization': 'Bearer http-error-fixture',
                    'Content-Type': 'application/json',
                })
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with pytest.raises(urllib.error.HTTPError) as caught:
                    opener.open(req, timeout=10)
                with caught.value as error:
                    assert error.code == 500
                    assert error.headers.get_content_type() == 'text/plain'
                    assert error.headers['X-Content-Type-Options'] == 'nosniff'
                    assert error.read() == b'Internal server error'
            await asyncio.to_thread(request)
    asyncio.run(run())
