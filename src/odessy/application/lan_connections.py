"""Coordinator connection commands, independent of model sessions."""
import asyncio
import json
from pathlib import Path
import socket

from odessy.setup.lan_pair import scan_execution_hosts, request_pairing, PairingFailed
from odessy.tools.lan_executor import LanConnection, current, select, clear


class Connections:
    def __init__(self, selection_path=None):
        self.hosts = {}
        self.busy = False
        self.stage = 'disconnected'
        self.selection_path = Path(selection_path or Path.home() / '.config' / 'odessy' / 'executor-selection.json')

    async def scan(self):
        rows = await asyncio.to_thread(scan_execution_hosts)
        self.hosts = {row['id']: row for row in rows if row.get('id')}
        return {'hosts': list(self.hosts.values()), 'connection': current().public() if current() else None}

    async def connect(self, device_id):
        if self.busy:
            raise ValueError('Another connection request is already pending')
        if current() is not None:
            raise ValueError('Disconnect the selected execution host before changing it')
        host = self.hosts.get(device_id)
        if host is None:
            raise ValueError('Device no longer listed. Refresh nearby execution hosts.')
        self.busy = True
        self.stage = 'waiting_for_approval'
        try:
            result = await request_pairing(host['host'], host['port'], socket.gethostname(), device_id=device_id)
            self.stage = 'connecting'
            connection = LanConnection(device_id, host['name'], result['url'], result['workspace'], result['credential'])
            # Acceptance is not readiness. Verify the actual authenticated MCP path.
            async with connection.client() as client:
                tools = await asyncio.wait_for(client.list_tools(), timeout=15)
            count = len(getattr(tools, 'tools', tools))
            self.selection_path.parent.mkdir(parents=True, exist_ok=True)
            # Only discovery metadata is durable. Credentials never enter this file.
            if self.selection_path.is_symlink():
                raise ValueError('Saved execution host selection must not be a symbolic link')
            self.selection_path.write_text(json.dumps(host, indent=2), encoding='utf-8')
            select(connection)
            self.stage = 'connected'
            return {'state': 'connected', 'host': connection.public(), 'tools': count}
        except asyncio.CancelledError:
            clear()
            raise
        except (ValueError, PairingFailed):
            raise
        except Exception:
            raise PairingFailed('Request was accepted, but the tool connection failed. Restart sharing on the execution host and reconnect.') from None
        finally:
            self.busy = False
            if current() is None:
                self.stage = 'failed'

    def disconnect(self):
        clear()
        self.stage = 'disconnected'
        return {'state': 'disconnected'}


def register(app):
    from fastapi import HTTPException, Request
    from odessy.setup.lan_pair import PairingDeclined
    manager = Connections()
    app.state.lan_connections = manager

    @app.get('/v1/execution-hosts/status')
    async def execution_host_status():
        return {'state': manager.stage, 'connection': current().public() if current() else None}

    @app.get('/v1/execution-hosts')
    async def execution_hosts():
        try:
            return await manager.scan()
        except Exception:
            raise HTTPException(503, 'Nearby execution hosts could not be detected. Check Wi-Fi and discovery dependencies.') from None

    @app.post('/v1/execution-hosts/connect')
    async def connect_execution_host(request: Request):
        if any(controller.running for controller in app.state.sessions.values()):
            raise HTTPException(409, 'Stop the active turn before changing execution hosts')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096:
                raise HTTPException(400, 'Connection request is too large')
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) != {'id'} or not isinstance(data['id'], str):
                raise ValueError('Select a discovered execution host')
            return await manager.connect(data['id'])
        except PairingDeclined:
            raise HTTPException(403, 'Execution host declined or did not answer. Press Connect to ask again.') from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except PairingFailed as exc:
            raise HTTPException(503, str(exc)) from None

    @app.post('/v1/execution-hosts/disconnect')
    async def disconnect_execution_host():
        if any(controller.running for controller in app.state.sessions.values()):
            raise HTTPException(409, 'Stop the active turn before disconnecting execution')
        return manager.disconnect()
