"""Session-scoped consent and cancellation, independent of the MCP stdio loop."""
from __future__ import annotations
import asyncio
import concurrent.futures
import json
import secrets
import socket
import socketserver
import threading
from odessy.authority.command_bridge import terminate_process_tree
from odessy.execution.contract import ProviderError

LIMIT = 65536

class ToolControl:
    def __init__(self, loop, ask):
        self.loop, self.ask = loop, ask
        self.token = secrets.token_hex(32)
        self.lock = threading.RLock()
        self.epoch = 0
        self.active = False
        self.running = {}
        self.closed = False
        self.slots = threading.BoundedSemaphore(8)
        self.consent_slot = threading.Lock()
        self.clean = threading.Event()
        self.clean.set()
        owner = self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.request.settimeout(2)
                try:
                    raw = self.rfile.readline(LIMIT + 1)
                    if len(raw) > LIMIT or not raw.endswith(b'\n'):
                        return
                    message = json.loads(raw)
                    if not isinstance(message, dict):
                        return
                    token = message.get('token')
                    if not isinstance(token, str) or not secrets.compare_digest(token.encode(), owner.token.encode()):
                        return
                    result = owner.handle(message)
                    self.wfile.write(json.dumps(result).encode() + b'\n')
                except (OSError, ValueError, TypeError):
                    return
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = False
            def process_request(self, request, client_address):
                if not owner.slots.acquire(blocking=False):
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, client_address)
                except BaseException:
                    owner.slots.release()
                    raise
            def process_request_thread(self, request, client_address):
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    owner.slots.release()
        self.server = Server(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def address(self):
        return self.server.server_address[1], self.token

    def begin(self):
        with self.lock:
            if self.closed or self.running:
                raise RuntimeError("tool cleanup is still pending or the channel is closed")
            self.epoch += 1
            self.active = True

    def cancel(self):
        with self.lock:
            self.active = False
            self.epoch += 1

    def handle(self, message):
        with self.lock:
            epoch, active = self.epoch, self.active
        if message.get('kind') in ('spawn', 'finished'):
            pid, generation = message.get('pid'), message.get('epoch')
            if (not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
                    or not isinstance(generation, int) or isinstance(generation, bool)):
                return {'ok': False}
            with self.lock:
                if message['kind'] == 'spawn':
                    if generation != self.epoch or not self.active:
                        return {'ok': False}
                    self.running[pid] = generation
                    self.clean.clear()
                elif self.running.get(pid) == generation:
                    del self.running[pid]
                    if not self.running:
                        self.clean.set()
            return {'ok': True}
        if message.get('kind') == 'state':
            return {'epoch': epoch, 'active': active}
        if not active or message.get('epoch') != epoch:
            return {'approved': False, 'reason': 'cancelled'}
        details = message.get('details')
        if message.get('kind') != 'consent' or not isinstance(details, dict):
            return {'approved': False, 'reason': 'invalid request'}
        if not self.consent_slot.acquire(blocking=False):
            return {'approved': False, 'reason': 'busy'}
        try:
            future = asyncio.run_coroutine_threadsafe(self.ask(details), self.loop)
        except RuntimeError:
            self.consent_slot.release()
            return {'approved': False, 'reason': 'closed'}
        try:
            for _ in range(3000):
                with self.lock:
                    if not self.active or self.epoch != epoch:
                        return {'approved': False, 'reason': 'cancelled'}
                try:
                    approved, reason = future.result(timeout=.1)
                    with self.lock:
                        approved = approved is True and self.active and self.epoch == epoch
                    return {'approved': approved, 'reason': reason if approved else 'cancelled'}
                except concurrent.futures.TimeoutError:
                    continue
            return {'approved': False, 'reason': 'timeout'}
        except Exception:
            return {'approved': False, 'reason': 'unavailable'}
        finally:
            self.consent_slot.release()
            if not future.done():
                future.cancel()

    def close(self):
        self.cancel()
        self.closed = True
        self.server.shutdown()
        self.server.server_close()

class ToolClient:
    def __init__(self, port, token):
        self.port, self.token = port, token
        self.local = threading.local()

    def request(self, **message):
        message['token'] = self.token
        payload = json.dumps(message).encode() + b'\n'
        if len(payload) > LIMIT:
            raise ValueError('control request too large')
        with socket.create_connection(('127.0.0.1', self.port), timeout=2) as sock:
            sock.settimeout(305 if message.get('kind') == 'consent' else 2)
            sock.sendall(payload)
            with sock.makefile('rb') as reader:
                raw = reader.readline(LIMIT + 1)
                if len(raw) > LIMIT or not raw.endswith(b'\n'):
                    raise ValueError('invalid control response')
                return json.loads(raw)

    def enter(self):
        try:
            state = self.request(kind='state')
            self.local.epoch = state['epoch']
            return bool(state['active'])
        except Exception:
            return False

    def consent(self, details):
        try:
            if not hasattr(self.local, 'epoch') and not self.enter():
                return False, 'cancelled'
            result = self.request(kind='consent', epoch=self.local.epoch, details=details)
            return result.get('approved') is True, result.get('reason', 'unavailable')
        except Exception:
            return False, 'unavailable'

    def before_spawn(self):
        try:
            state = self.request(kind='state')
            live = state['active'] and state['epoch'] == getattr(self.local, 'epoch', None)
        except Exception:
            live = False
        if not live:
            raise ProviderError('the tool turn was cancelled or its control channel is unavailable')

    def on_spawn(self, process):
        epoch = getattr(self.local, 'epoch', None)
        try:
            registered = self.request(kind='spawn', pid=process.pid, epoch=epoch)
            if registered.get('ok') is not True:
                terminate_process_tree(process)
                return
        except Exception:
            terminate_process_tree(process)
            return
        def watch():
            try:
                self._watch(process, epoch)
            finally:
                try:
                    if process.poll() is not None:
                        self.request(kind='finished', pid=process.pid, epoch=epoch)
                except Exception:
                    pass
        threading.Thread(target=watch, daemon=True).start()

    def _watch(self, process, epoch):
        while process.poll() is None:
            try:
                state = self.request(kind='state')
                live = state['active'] and state['epoch'] == epoch
            except Exception:
                live = False
            if not live:
                terminate_process_tree(process)
                # Do not acknowledge cleanup when termination failed.
                if process.poll() is None:
                    threading.Event().wait(.05)
                    continue
                return
            threading.Event().wait(.05)
