import asyncio
import io
import json

import pytest

from odessy.application.executor import HostSettings, TerminalApproval, create_listener


def test_device_id_persists_and_settings_never_contain_credentials(tmp_path):
    first = HostSettings.load(tmp_path / 'host.json', workspace=tmp_path / 'workspace')
    second = HostSettings.load(tmp_path / 'host.json')
    assert first.device_id == second.device_id
    assert first.workspace == second.workspace
    assert 'token' not in (tmp_path / 'host.json').read_text()


@pytest.mark.parametrize('answer,accepted', [('Y\n', True), ('y\n', True), ('N\n', False), ('yes\n', False), ('', False)])
def test_only_y_authorizes(answer, accepted):
    async def run():
        output = io.StringIO()
        approval = TerminalApproval(io.StringIO(answer), output)
        assert await approval.ask('Jetson', '/test', timeout=.5) is accepted
        assert '/test' in output.getvalue()
    asyncio.run(run())


def test_listener_is_limited_and_approval_returns_real_mcp_url(tmp_path):
    import urllib.request
    import urllib.error
    async def run():
        settings = HostSettings.load(tmp_path / 'host.json', workspace=tmp_path / 'workspace')
        approval = TerminalApproval(io.StringIO('Y\n'), io.StringIO())
        listener = create_listener(settings, approval, 'execution-secret', 'http://127.0.0.1:9001/mcp',
                                   host='127.0.0.1', networks=['127.0.0.0/8'])
        def request():
            url = f'http://127.0.0.1:{listener.server_address[1]}'
            for path in ('/v1/settings', '/v1/pairing/approve'):
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(url + path)
                assert error.value.code == 404
            req = urllib.request.Request(url + '/v1/pairing/request',
                data=json.dumps({'name': 'Jetson', 'device_id': settings.device_id}).encode(),
                headers={'Content-Type': 'application/json'})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=3) as response:
                return json.load(response)
        try:
            result = await asyncio.to_thread(request)
            assert result['credential'] == 'execution-secret'
            assert result['url'].endswith('/mcp')
            assert result['device_id'] == settings.device_id
            assert listener.accepted
        finally:
            await asyncio.to_thread(listener.shutdown)
            listener.server_close()
    asyncio.run(run())
