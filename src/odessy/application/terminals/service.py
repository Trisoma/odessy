"""Opt-in Node/ConPTY owner. 07 must supply reviewed packaged module/helper paths.

No shell starts on construction, registration, output browsing or reconnect.
The callback must screen the exact shell grant with existing policy/hooks/broker.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import uuid
from pathlib import Path


class TerminalError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise TerminalError(400, 'Invalid terminal dimensions, revision or offset')
    return value


class LocalTerminalService:
    available = True  # Explicit opt-in owner only; never the global capability.

    def __init__(self, *, inspector, request_grant, node, module_root, helper,
                 mode_revision=None):
        self.inspector, self.grant = inspector, request_grant
        self.node, self.module_root, self.helper = map(Path, (node, module_root, helper))
        try:
            metadata = json.loads((self.module_root / 'node-pty/package.json').read_text('utf-8'))
            if metadata.get('version') != '1.1.0' or not self.node.is_file() or not self.helper.is_file():
                raise ValueError('Unqualified PTY inputs')
            receipt = json.loads(self.helper.with_name('dependency-receipt.json').read_text('utf-8'))
            if os.name == 'nt':
                for entry in receipt['native_sha256']:
                    native = self.module_root / 'node-pty' / entry['path']
                    if hashlib.sha256(native.read_bytes()).hexdigest() != entry['sha256']:
                        raise ValueError('PTY native hash mismatch')
        except (OSError, ValueError, KeyError, TypeError):
            raise TerminalError(501, 'Pinned node-pty 1.1.0 and the owned helper must be packaged first') from None
        self.mode_revision = mode_revision
        self.handles = {}
        self._admission = asyncio.Lock()
        self.closed_sessions = set()

    def _binding(self, sid):
        if sid in self.closed_sessions:
            raise TerminalError(409, 'Session has closed; create a fresh live session')
        try:
            binding = self.inspector.binding(sid)
            if binding.local is not True or binding.is_authorized() is not True:
                raise TerminalError(403, 'Local execution/workspace grant required')
            root = Path(binding.root_path)
            info = root.stat()
            if (info.st_dev, info.st_ino) != binding.root_identity:
                raise TerminalError(409, 'Workspace changed')
            return binding
        except TerminalError:
            raise
        except Exception as error:
            raise TerminalError(getattr(error, 'status', 403), 'Live terminal owner unavailable') from None

    def _revision(self, sid):
        return self.mode_revision(sid) if self.mode_revision else None

    def _wire(self, row):
        return {'version': 1, 'id': row['id'], 'session_id': row['sid'],
                'service_instance_id': row['instance'], 'revision': row['revision'],
                'status': row['status'], 'exit_code': row['exit_code']}

    async def _row(self, sid, tid, revision=None):
        row = self.handles.get(tid)
        if row is None or row['sid'] != sid:
            raise TerminalError(404, 'Terminal not found for this session')
        try:
            binding = self._binding(sid)
            if binding.identity() != row['identity'] or self._revision(sid) != row['mode_revision']:
                raise TerminalError(409, 'Terminal authority changed; create a fresh terminal')
        except TerminalError:
            await self._stop(row)
            raise
        if revision is not None and _integer(revision, 0, 2**53) != row['revision']:
            raise TerminalError(409, 'Terminal revision changed')
        return row

    async def _pump(self, row):
        try:
            while line := await row['process'].stdout.readline():
                event = json.loads(line)
                if event['event'] == 'output':
                    row['buffer'] += event['text'].encode('utf-8')
                    if len(row['buffer']) > 200 * 1024:
                        excess = len(row['buffer']) - 200 * 1024
                        while excess < len(row['buffer']) and row['buffer'][excess] & 0xc0 == 0x80:
                            excess += 1
                        row['buffer'] = row['buffer'][excess:]
                        row['start'] += excess
                elif event['event'] == 'ready':
                    row['ready'].set_result(True)
                elif event['event'] == 'exit':
                    row['status'], row['exit_code'] = 'exited', event['code']
                elif event.get('id') in row['pending']:
                    future = row['pending'][event['id']]
                    if not future.done():
                        if event['event'] == 'error':
                            future.set_exception(TerminalError(503, 'Native terminal operation failed'))
                        else:
                            future.set_result(True)
        except (ValueError, OSError, asyncio.LimitOverrunError):
            row['status'] = 'unknown'
        finally:
            if not row['ready'].done():
                row['ready'].set_exception(TerminalError(503, 'Native terminal failed to start'))
            for future in row['pending'].values():
                if not future.done():
                    future.set_exception(TerminalError(503, 'Terminal ended before acknowledgment'))
            if row['status'] == 'running':
                row['status'] = 'unknown'

    async def _send(self, row, op, **payload):
        if row['status'] != 'running':
            raise TerminalError(409, 'Terminal has ended; input was not replayed')
        key = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        row['pending'][key] = future
        try:
            row['process'].stdin.write((json.dumps({'op': op, 'id': key, **payload}) + '\n').encode())
            await row['process'].stdin.drain()
            await asyncio.wait_for(future, 5)
        except (OSError, asyncio.TimeoutError) as error:
            raise TerminalError(503, 'Terminal acknowledgment unavailable; do not replay input') from error
        finally:
            row['pending'].pop(key, None)

    async def create(self, sid, *, columns=80, rows=24):
        _integer(columns, 20, 400)
        _integer(rows, 5, 200)
        async with self._admission:
            if len(self.handles) >= 64 or sum(r['status'] == 'running' for r in self.handles.values()) >= 8:
                raise TerminalError(409, 'Terminal resource limit reached')
            binding = self._binding(sid)
            identity, mode = binding.identity(), self._revision(sid)
            plan = {'action': 'terminal.create', 'session_id': sid, 'scopes': ['authorization'],
                    'workspace_id': binding.workspace_id, 'columns': columns, 'rows': rows,
                    'interactive_shell': True,
                    'disclosure': 'Local OS shell runs as your user. Commands are not individually intercepted or confined to the workspace.'}
            approved = self.grant(sid, plan) if self.grant else False
            if inspect.isawaitable(approved):
                approved = await approved
            if approved is not True:
                raise TerminalError(403, 'Interactive shell grant refused')
            if self._binding(sid).identity() != identity or self._revision(sid) != mode:
                raise TerminalError(409, 'Authority changed during shell approval')
            # Carry only OS shell essentials; never inherit API/provider credentials.
            names = {'systemroot', 'windir', 'comspec', 'path', 'pathext', 'temp', 'tmp',
                     'userprofile', 'home', 'lang', 'term'}
            environment = {key: value for key, value in os.environ.items() if key.lower() in names}
            environment['NODE_PATH'] = str(self.module_root.resolve())
            process = await asyncio.create_subprocess_exec(str(self.node), str(self.helper),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, env=environment, limit=256 * 1024)
            row = {'id': uuid.uuid4().hex, 'sid': sid, 'instance': binding.service_instance_id,
                   'identity': identity, 'mode_revision': mode, 'revision': 0,
                   'process': process, 'status': 'running', 'exit_code': None,
                   'buffer': b'', 'start': 0, 'pending': {},
                   'ready': asyncio.get_running_loop().create_future(), 'lock': asyncio.Lock()}
            self.handles[row['id']] = row
            row['pump'] = asyncio.create_task(self._pump(row))
            try:
                process.stdin.write((json.dumps({'op': 'create', 'columns': columns,
                    'rows': rows, 'cwd': str(binding.root_path)}) + '\n').encode())
                await process.stdin.drain()
                await asyncio.wait_for(row['ready'], 10)
                await self._row(sid, row['id'])
                row['watcher'] = asyncio.create_task(self._watch(row))
                return self._wire(row)
            except BaseException:
                await self._stop(row)
                raise

    async def _watch(self, row):
        while row['process'].returncode is None:
            await asyncio.sleep(.2)
            try:
                await self._row(row['sid'], row['id'])
            except TerminalError:
                return

    async def output(self, sid, tid, *, after=0):
        _integer(after, 0, 2**53)
        row = await self._row(sid, tid)
        end = row['start'] + len(row['buffer'])
        if after > end:
            raise TerminalError(409, 'Output cursor belongs to another terminal state')
        offset = max(after, row['start']) - row['start']
        while offset < len(row['buffer']) and row['buffer'][offset] & 0xc0 == 0x80:
            offset += 1
        chunk = row['buffer'][offset:offset + 65536]
        text = chunk.decode('utf-8', errors='ignore')
        return {'version': 1, 'handle': self._wire(row), 'output': text,
                'next_offset': row['start'] + offset + len(text.encode('utf-8')),
                'truncated': after < row['start']}

    async def _mutate(self, sid, tid, revision, op, **payload):
        row = await self._row(sid, tid, revision)
        async with row['lock']:
            await self._row(sid, tid, revision)
            await self._send(row, op, **payload)
            row['revision'] += 1
            return self._wire(row)

    async def input(self, sid, tid, *, text, expected_revision):
        if not isinstance(text, str) or len(text.encode('utf-8')) > 16384:
            raise TerminalError(400, 'Terminal input exceeds 16 KiB')
        # Input consumes no handle revision: concurrent stale revisions still refuse.
        row = await self._row(sid, tid, expected_revision)
        async with row['lock']:
            await self._row(sid, tid, expected_revision)
            await self._send(row, 'input', text=text)

    async def resize(self, sid, tid, *, columns, rows, expected_revision):
        _integer(columns, 20, 400)
        _integer(rows, 5, 200)
        return await self._mutate(sid, tid, expected_revision, 'resize', columns=columns, rows=rows)

    async def interrupt(self, sid, tid, *, expected_revision):
        return await self._mutate(sid, tid, expected_revision, 'interrupt')

    async def _stop(self, row):
        watcher = row.get('watcher')
        if watcher is not None and watcher is not asyncio.current_task():
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        process = row['process']
        if process.returncode is None:
            try:
                await self._send(row, 'close')
            except Exception:
                if process.stdin:
                    process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        await asyncio.gather(row['pump'], return_exceptions=True)
        row['status'] = 'exited'

    async def close(self, sid, tid, *, expected_revision):
        row = await self._row(sid, tid, expected_revision)
        async with row['lock']:
            await self._stop(row)
            row['revision'] += 1
            return self._wire(row)

    async def close_session(self, sid):
        self.closed_sessions.add(sid)
        await asyncio.gather(*(self._stop(row) for row in self.handles.values() if row['sid'] == sid))
