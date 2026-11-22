"""Temporary, explicitly approved OpenSSH execution. No local command fallback."""

import asyncio
import base64
import hashlib
import shlex
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace

from odessy.authority.redaction import redact_text
from odessy.ssh.identity import SshHost, read_identity, ssh_argv

_active = None


def current():
    return _active


def select(connection):
    global _active
    if _active is not None:
        raise PermissionError('Disconnect the previous SSH execution target first.')
    _active = connection


def clear():
    global _active
    connection, _active = _active, None
    if connection is not None:
        connection.revoked = True
        for process in tuple(connection.processes):
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass


@dataclass
class SshExecutionConnection:
    host: SshHost
    name: str
    fingerprint: str
    binary: str = 'ssh'
    lease: object = field(default=None, repr=False)
    askpass: str = field(default='', repr=False)
    public_keys: tuple = field(default=(), repr=False)
    revoked: bool = False
    processes: set = field(default_factory=set, repr=False)

    def public(self):
        return {'target': self.host.alias, 'name': self.name, 'port': self.host.port,
                'fingerprint': self.fingerprint, 'execution': not self.revoked,
                'platform': 'POSIX shell', 'sandbox': 'none', 'temporary': True}

    def server(self):
        return SshServer(self)


def matching_keys(keys, fingerprint):
    """Use only the key displayed at selection, even if known_hosts has others."""
    result = []
    for kind, encoded in keys:
        digest = base64.b64encode(hashlib.sha256(
            base64.b64decode(encoded, validate=True)).digest()).decode().rstrip('=')
        if fingerprint == 'SHA256:' + digest:
            result.append((kind, encoded))
    return tuple(result)


class SshServer:
    prefix = ''

    def __init__(self, connection):
        self.connection = connection
        self.spawn = asyncio.create_subprocess_exec
        self.config = SimpleNamespace(name=connection.host.alias, target_metadata={
            'method': 'ssh', 'address': connection.host.hostname,
            'user': connection.host.user, 'port': str(connection.host.port),
            'fingerprint': connection.fingerprint, 'connection_id': str(id(connection)),
            'workspace': 'remote login directory; no sandbox'})

    def preflight(self):
        c = self.connection
        if current() is not c or c.revoked:
            raise PermissionError('SSH target disconnected or changed. Reconnect and review again.')
        if c.lease is not None and not c.lease.active:
            raise PermissionError('SSH password expired. Reconnect before running commands.')

    async def list_tools(self):
        self.preflight()
        from odessy.tools.tool_contracts import TOOL_REQUEST_SCHEMAS
        return [SimpleNamespace(name='ssh.run', description='Run an approved command on ' +
                                self.connection.host.alias,
                                inputSchema=TOOL_REQUEST_SCHEMAS['ssh.run'])]

    async def call_tool(self, name, arguments, *, before_dispatch=None):
        self.preflight()
        if name != 'ssh.run':
            raise ValueError('Unknown SSH tool')
        command, timeout = arguments.get('command'), arguments.get('timeout', 20)
        if (not isinstance(command, str) or not command.strip() or len(command) > 8192
                or any(ch in command for ch in '\x00\r\n')):
            raise ValueError('SSH requires one bounded, nonempty command line')
        if type(timeout) is not int or not 1 <= timeout <= 60:
            raise ValueError('SSH timeout must be 1–60 seconds')
        if set(arguments) - {'command', 'timeout'}:
            raise ValueError('Unsupported SSH arguments')
        c = self.connection
        identity = await asyncio.to_thread(read_identity, c.host, ssh=c.binary)
        if c.fingerprint not in {fp for _, fp in identity.fingerprints}:
            raise PermissionError('Trusted SSH identity changed. Reconnect and verify it.')
        keys = matching_keys(c.public_keys, c.fingerprint)
        if not keys:
            raise PermissionError('Selected SSH public key is unavailable. Reconnect.')
        # Pin endpoint AND public key. Changes to user SSH configuration cannot
        # redirect a previously approved command to another host or local process.
        with tempfile.TemporaryDirectory(prefix='odessy-ssh-') as directory:
            known = Path(directory) / 'known_hosts'
            endpoint = c.host.hostname if c.host.port == 22 else f'[{c.host.hostname}]:{c.host.port}'
            known.write_text(''.join(f'{endpoint} {kind} {key}\n' for kind, key in keys), encoding='ascii')
            args = list(ssh_argv(replace(c.host, config_path=None),
                                 'sh -c ' + shlex.quote(command), ssh=c.binary))
            options = ['-F', 'none', '-o', 'HostName=' + c.host.hostname,
                       '-o', 'User=' + c.host.user, '-o', 'PermitLocalCommand=no',
                       '-o', 'ProxyCommand=none', '-o', 'ProxyJump=none',
                       '-o', 'ControlPath=none', '-o', 'ForwardX11=no',
                       '-o', 'UserKnownHostsFile="' + known.as_posix() + '"',
                       '-o', 'GlobalKnownHostsFile=none', '-o', 'ConnectionAttempts=1']
            for identity_file in c.host.identity_files:
                options += ['-i', identity_file]
            args[1:1] = options
            env = None
            if c.lease is not None:
                option = next(i + 1 for i, value in enumerate(args[:-1])
                              if value == '-o' and args[i + 1] == 'BatchMode=yes')
                args[option] = 'BatchMode=no'
                args[1:1] = ['-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no',
                             '-o', 'KbdInteractiveAuthentication=no', '-o', 'NumberOfPasswordPrompts=1']
                env = c.lease.environment(c.askpass)
            self.preflight()
            if before_dispatch:
                before_dispatch()
            redaction_password = c.lease.password if c.lease is not None else ''
            process = await self.spawn(*args, stdin=asyncio.subprocess.DEVNULL,
                                       stdout=asyncio.subprocess.PIPE,
                                       stderr=asyncio.subprocess.PIPE, env=env)
            c.processes.add(process)
            buffers = [bytearray(), bytearray()]
            truncated = [False, False]

            async def drain(stream, index):
                while chunk := await stream.read(4096):
                    # Retain enough lookahead to redact a secret crossing the cap.
                    remaining = 32768 + len(redaction_password.encode('utf-8')) - len(buffers[index])
                    buffers[index].extend(chunk[:remaining])
                    truncated[index] |= len(chunk) > remaining

            drains = [asyncio.create_task(drain(process.stdout, 0)),
                      asyncio.create_task(drain(process.stderr, 1))]
            status = 'ok'
            try:
                self.preflight()
                await asyncio.wait_for(process.wait(), timeout)
                self.preflight()
                if process.returncode:
                    status = 'failed'
            except asyncio.TimeoutError:
                status = 'timed_out'
            finally:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                await process.wait()
                try:
                    await asyncio.wait_for(asyncio.gather(*drains), 2)
                except asyncio.TimeoutError:
                    truncated[:] = [True, True]
                finally:
                    c.processes.discard(process)
            def output(data):
                if redaction_password:
                    data = data.replace(redaction_password.encode('utf-8'), b'<redacted>')
                text = data[:32768].decode('utf-8', errors='replace')
                return redact_text(text)
            result = {'status': status, 'exit_code': process.returncode,
                      'stdout': output(buffers[0]), 'stderr': output(buffers[1]),
                      'truncated': any(truncated) or any(len(data) > 32768 for data in buffers), 'origin': 'ssh', 'target': c.host.alias,
                      'port': c.host.port,
                      'cancellation': 'SSH channel closes on Stop; remote descendant termination is not guaranteed.'}
            return SimpleNamespace(structured_content=result, is_error=status != 'ok')
