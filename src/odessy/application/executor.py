"""Experimental trusted-LAN executor. No model, desktop or browser required."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import uuid

from odessy.application import execution_access
from odessy.tools.node_mcp import NodeToolServerHttp


@dataclass(frozen=True)
class HostSettings:
    device_id: str
    name: str
    workspace: Path

    @classmethod
    def load(cls, path, *, workspace=None):
        path = Path(path)
        if path.is_symlink():
            raise ValueError('Execution host settings must not be a symbolic link')
        if path.exists():
            if path.stat().st_size > 65536:
                raise ValueError('Execution host settings are too large')
            data = json.loads(path.read_text(encoding='utf-8'))
            device_id = str(uuid.UUID(data['device_id']))
            root = Path(workspace or data['workspace']).expanduser().resolve()
        else:
            device_id = str(uuid.uuid4())
            root = Path(workspace or Path.home() / 'Documents' / 'Odessy-execution').expanduser().resolve()
            root.mkdir(parents=True, exist_ok=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x', encoding='utf-8') as stream:
                json.dump({'device_id': device_id, 'workspace': str(root)}, stream)
        if not root.is_dir():
            raise ValueError('The shared workspace must be an existing directory')
        return cls(device_id, socket.gethostname(), root)


class TerminalApproval:
    """One keyboard reader; late answers can never approve a newer request."""
    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout
        self._busy = threading.Lock()

    async def ask(self, name, workspace, *, timeout=60):
        if not self._busy.acquire(blocking=False):
            return False
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def read():
            try:
                self.stdout.write(f'\n{name} requests access to {workspace}. Allow connection? [Y/N] ')
                self.stdout.flush()
                answer = self.stdin.readline().strip().lower()
            except (OSError, ValueError):
                answer = ''
            finally:
                self._busy.release()
            def settle():
                if not future.done():
                    future.set_result(answer == 'y')
            try:
                loop.call_soon_threadsafe(settle)
            except RuntimeError:
                pass

        threading.Thread(target=read, name='odessy-executor-keyboard', daemon=True).start()
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            self.stdout.write('\nRequest expired. Press Enter to dismiss the old prompt before another request.\n')
            self.stdout.flush()
            return False


def lan_interfaces():
    """IPv4 LAN interfaces, preferring physical interfaces over VM adapters."""
    try:
        import ifaddr
    except ImportError as exc:
        raise RuntimeError('LAN discovery requires the packaged pairing dependencies (odessy[pairing]).') from exc
    rows = []
    for adapter in ifaddr.get_adapters():
        label = (adapter.nice_name + ' ' + adapter.name).lower()
        virtual = any(word in label for word in ('vethernet', 'vmware', 'virtual', 'docker', 'wsl', 'vbox', 'loopback'))
        for entry in adapter.ips:
            if not isinstance(entry.ip, str):
                continue
            address = ipaddress.ip_address(entry.ip)
            if address.version != 4 or address.is_loopback or address.is_link_local or address.is_unspecified:
                continue
            network = ipaddress.ip_network(f'{entry.ip}/{entry.network_prefix}', strict=False)
            rows.append((virtual, entry.ip, str(network)))
    return [(ip, network) for _, ip, network in sorted(set(rows))]


def create_listener(settings, approval, credential, mcp_url, *, host, networks, port=0):
    """Expose only the bounded connection request. No admin routes or SSE."""
    loop = asyncio.get_running_loop()
    allowed = [ipaddress.ip_network(network) for network in networks]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log remote input or credentials.

        def reply(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            if self.path != '/v1/pairing/request':
                return self.reply(404, {'error': 'Not found'})
            try:
                peer = ipaddress.ip_address(self.client_address[0])
                if not any(peer in network for network in allowed):
                    return self.reply(403, {'error': 'Use the same local network'})
                self.connection.settimeout(5)
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4096:
                    return self.reply(400, {'error': 'Invalid request size'})
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict) or set(data) - {'name', 'device_id'}:
                    return self.reply(400, {'error': 'Invalid request'})
                name = data.get('name')
                if not isinstance(name, str) or not name.strip() or len(name) > 64 or any(ord(c) < 32 or ord(c) == 127 for c in name):
                    return self.reply(400, {'error': 'Invalid coordinator name'})
                if data.get('device_id') != settings.device_id:
                    return self.reply(409, {'error': 'Device changed; refresh discovery'})
                if not self.server.admission.acquire(blocking=False):
                    return self.reply(409, {'error': 'Another coordinator or request is already active'})
                try:
                    if self.server.accepted:
                        return self.reply(409, {'error': 'A coordinator is already connected. Restart sharing to change it.'})
                    decision = asyncio.run_coroutine_threadsafe(approval.ask(name, settings.workspace), loop)
                    try:
                        accepted = decision.result(timeout=65)
                    except TimeoutError:
                        decision.cancel()
                        accepted = False
                    if not accepted:
                        return self.reply(403, {'error': 'Connection declined or expired'})
                    self.server.accepted = True
                    return self.reply(200, {'credential': credential, 'url': mcp_url,
                        'workspace': str(settings.workspace), 'device_id': settings.device_id})
                finally:
                    self.server.admission.release()
            except (ValueError, OSError):
                self.reply(400, {'error': 'Invalid or interrupted connection request'})

    class Listener(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 2
        def __init__(self):
            self.admission = threading.Lock()
            self.accepted = False
            self.slots = threading.BoundedSemaphore(4)
            super().__init__((host, port), Handler)

        def process_request(self, request, client_address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            super().process_request(request, client_address)

        def process_request_thread(self, request, client_address):
            request.settimeout(5)
            try:
                super().process_request_thread(request, client_address)
            finally:
                self.slots.release()

    server = Listener()
    threading.Thread(target=server.serve_forever, name='odessy-executor-requests', daemon=True).start()
    return server


async def run(settings, *, host=None):
    from types import SimpleNamespace
    from .pairing import register_mdns
    interfaces = lan_interfaces()
    if not interfaces:
        raise RuntimeError('No LAN IPv4 interface is available. Connect Wi-Fi or Ethernet and retry.')
    address = host or interfaces[0][0]
    networks = [network for ip, network in interfaces if ip == address]
    if not networks:
        raise ValueError('Select an address belonging to this machine')
    secret = secrets.token_urlsafe(32)
    # One place decides what this credential is: stopping the executor calls
    # reset(), which is what invalidates it.
    execution_access.reset()
    execution_access.configure(secret, str(settings.workspace))
    print(f'Odessy execution host: {settings.name}\nDevice ID: {settings.device_id}\nWorkspace: {settings.workspace}', flush=True)
    print('Experimental trusted-LAN mode (unencrypted). Only approve requests you expect. Close this terminal or press Ctrl+C to stop sharing.', flush=True)
    async with NodeToolServerHttp(settings.workspace, host=address, extra_env={'ODESSY_MCP_TOKEN': secret}) as url:
        server = create_listener(settings, TerminalApproval(), secret, url, host=address, networks=networks)
        app = SimpleNamespace(state=SimpleNamespace())
        try:
            registered = await asyncio.to_thread(register_mdns, app, server.server_address[1],
                instance=f'{settings.name}-{settings.device_id[:8]}', address=address, device_id=settings.device_id)
            if registered is None:
                raise RuntimeError('Could not announce this execution host. Check multicast/discovery dependencies.')
            print('Ready. On the coordinator, open Devices and click Connect beside this host.', flush=True)
            await asyncio.Event().wait()
        finally:
            if getattr(app.state, 'zeroconf', None):
                await asyncio.to_thread(app.state.zeroconf.close)
            await asyncio.to_thread(server.shutdown)
            server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Odessy headless trusted-LAN execution host')
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--host', help='Advanced: local LAN IPv4 interface to use')
    parser.add_argument('--settings', type=Path, default=Path.home() / '.config' / 'odessy' / 'execution-host.json')
    args = parser.parse_args(argv)
    try:
        workspace = args.workspace
        if not args.settings.exists() and workspace is None:
            default = Path.home() / 'Documents' / 'Odessy-execution'
            print(f'First launch. Share workspace [{default}]. Press Enter to use it, or type another directory:', flush=True)
            answer = sys.stdin.readline()
            if not answer:
                raise ValueError('No terminal input. Open the executor in a terminal.')
            workspace = Path(answer.strip()).expanduser() if answer.strip() else default
        settings = HostSettings.load(args.settings, workspace=workspace)
        asyncio.run(run(settings, host=args.host))
    except KeyboardInterrupt:
        print('\nSharing stopped. Connection credentials are no longer valid.', flush=True)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f'Execution host could not start: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
