"""Memory-only, explicitly selected trusted-LAN execution target."""
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace

_active = None


def current():
    return _active


def select(connection):
    global _active
    _active = connection


def clear():
    global _active
    _active = None


@dataclass(frozen=True)
class LanConnection:
    device_id: str
    name: str
    url: str
    workspace: str
    credential: str = field(repr=False)

    def headers(self):
        return {'Authorization': 'Bearer ' + self.credential}

    def public(self):
        return {'id': self.device_id, 'name': self.name, 'url': self.url, 'workspace': self.workspace}

    def server(self):
        return LanServer(self)

    @asynccontextmanager
    async def client(self):
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        async with httpx2.AsyncClient(headers=self.headers(), follow_redirects=False, timeout=15, trust_env=False) as http:
            async with Client(streamable_http_client(self.url, http_client=http)) as client:
                yield client


class LanServer:
    prefix = ''

    def __init__(self, connection):
        self.connection = connection
        self.config = SimpleNamespace(name=connection.name, target_metadata={
            'device_id': connection.device_id, 'endpoint': connection.url,
            'workspace': connection.workspace, 'connection_id': str(id(connection))})

    def _check(self):
        if current() is not self.connection:
            raise PermissionError('Execution target changed. Reconnect and review the action again.')

    async def list_tools(self):
        self._check()
        async with self.connection.client() as client:
            result = await client.list_tools()
        return getattr(result, 'tools', result)

    async def call_tool(self, name, arguments, *, before_dispatch=None):
        self._check()
        async with self.connection.client() as client:
            self._check()
            if before_dispatch:
                before_dispatch()
            return await client.call_tool(name, dict(arguments))
