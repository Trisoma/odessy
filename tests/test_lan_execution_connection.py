import asyncio
import json

import pytest

from odessy.tools import lan_executor


def test_connection_keeps_secret_out_of_metadata_and_uses_bearer_header():
    connection = lan_executor.LanConnection('device-id', 'Execution host',
        'http://127.0.0.1:9000/mcp', '/shared', 'sentinel-secret')
    assert 'sentinel-secret' not in repr(connection)
    assert 'sentinel-secret' not in json.dumps(connection.public())
    assert connection.headers() == {'Authorization': 'Bearer sentinel-secret'}


def test_changed_connection_invalidates_old_server_before_dispatch():
    async def run():
        first = lan_executor.LanConnection('a', 'Host', 'http://127.0.0.1:1/mcp', '/shared', 'first')
        lan_executor.select(first)
        server = first.server()
        lan_executor.clear()
        with pytest.raises(PermissionError):
            await server.call_tool('laptop.terminal.run', {'command': 'pwd'})
    asyncio.run(run())
