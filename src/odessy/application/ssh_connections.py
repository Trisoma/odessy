"""Application-level actions for one temporary or legacy saved SSH computer.

Polling, freshness, events and host projection belong to the Go monitoring service
(prompt 18). This module only lists/reviews aliases, runs one bounded connection
test, optionally persists legacy selections, and asks the existing
supervisor to reconfigure. It never edits known_hosts or private key files.
"""

import asyncio
import hashlib
import os
import shutil
from dataclasses import replace
from pathlib import Path

from odessy.config.config_file import (
    read_env_text,
    restore_env_text,
    update_env_values,
    user_config_file,
    validate_env_value,
)
from odessy.ssh.identity import (
    SshError,
    inspect_alias,
    list_aliases,
    probe_connection,
    resolve_host,
    _validate_alias,
)
from odessy.ssh.password import PasswordLease

class SshConnectionError(ValueError):
    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


class SshConnections:
    def __init__(self, monitor):
        self.monitor = monitor
        self._lock = asyncio.Lock()
        self._login_verified = False
        self._verified_identity = None
        self._execution_epoch = 0

    async def enable_execution(self):
        async with self._lock:
            return await self._enable_execution()

    async def _enable_execution(self):
        epoch = self._execution_epoch
        from odessy.tools.ssh_executor import current, select, SshExecutionConnection
        from odessy.tools.lan_executor import current as lan_target
        if lan_target() is not None:raise SshConnectionError('Disconnect experimental LAN execution first.',status=409)
        settings=self._settings()
        if not self._login_verified or not settings.ssh_alias:
            raise SshConnectionError('Connect and verify SSH login first.',status=409)
        from odessy.ssh.identity import read_identity
        try:
            host=await asyncio.to_thread(resolve_host,settings.ssh_alias,ssh=self._binary(settings),
                config_path=self._config(settings),port=settings.ssh_port or None)
            identity=await asyncio.to_thread(read_identity,host,ssh=self._binary(settings))
        except (SshError, OSError) as error:
            raise SshConnectionError(str(error),status=409) from None
        if not identity.known:raise SshConnectionError('SSH host identity is no longer trusted.',status=409)
        verified = self._verified_identity or {}
        if ((host.hostname, host.user, host.port) !=
                (verified.get('hostname'), verified.get('user'), verified.get('port'))
                or identity.primary_fingerprint not in verified.get('known_fingerprints', ())):
            raise SshConnectionError('SSH identity changed since login. Reconnect before selecting execution.',status=409)
        if host.proxy_jump:
            raise SshConnectionError('SSH execution preview supports direct connections; jump hosts are unavailable.',status=409)
        if not identity.public_keys:
            raise SshConnectionError('Trusted SSH public key unavailable. Reconnect.',status=409)
        old=current()
        if old is not None:raise SshConnectionError('Disconnect SSH execution before selecting it again.',status=409)
        lease=getattr(self.monitor,'ssh_password_lease',None)
        if settings.ssh_authentication=='password' and not getattr(lease,'active',False):
            raise SshConnectionError('SSH password expired. Reconnect.',status=409)
        helper=getattr(settings,'binary','')
        if epoch != self._execution_epoch:
            raise SshConnectionError('SSH selection was cancelled. Reconnect and review again.',status=409)
        if lan_target() is not None:
            raise SshConnectionError('LAN execution changed while selecting SSH. Disconnect it first.',status=409)
        connection=SshExecutionConnection(host,settings.ssh_display_name or host.alias,
            identity.primary_fingerprint,self._binary(settings),lease,helper,
            public_keys=identity.public_keys)
        select(connection)
        return {'execution':connection.public()}

    def execution_status(self):
        from odessy.tools.ssh_executor import current
        connection=current()
        return {'execution':connection.public() if connection else None}

    def disable_execution(self):
        from odessy.tools.ssh_executor import clear
        self._execution_epoch += 1
        clear()
        return {'execution':None}

    def _settings(self):
        settings = getattr(self.monitor, 'settings', None)
        if settings is None:
            raise SshConnectionError('Monitoring is unavailable on this service.', status=503)
        return settings

    def _binary(self, settings):
        return settings.ssh_binary or shutil.which('ssh')

    def _config(self, settings):
        return settings.ssh_config or None

    def _config_file(self):
        explicit = os.environ.get('ODESSY_CONFIG_FILE', '').strip()
        return Path(explicit) if explicit else user_config_file()

    @staticmethod
    def host_id(alias: str) -> str:
        return 'host_' + hashlib.sha256(alias.encode('utf-8')).hexdigest()

    def target(self) -> dict[str, object]:
        settings = self._settings()
        return {
            'schema_version': 1,
            'target': None if not settings.ssh_alias else {
                'alias': settings.ssh_alias,
                'host_id': settings.ssh_host_id,
                'display_name': settings.ssh_display_name or settings.ssh_alias,
                'monitoring': bool(settings.ssh_monitoring),
                'config': settings.ssh_config,
                'authentication': settings.ssh_authentication,
                'port': settings.ssh_port or None,
                'temporary': settings.ssh_temporary,
                'password_required': settings.ssh_authentication == 'password' and not bool(
                    getattr(getattr(self.monitor, 'ssh_password_lease', None), 'active', False)),
            },
        }

    async def aliases(self) -> dict[str, object]:
        settings = self._settings()
        try:
            values = await asyncio.to_thread(list_aliases, self._config(settings))
        except Exception as error:  # noqa: BLE001 - report, never dump config
            raise SshConnectionError(f'SSH configuration could not be read: {error}') from None
        saved = settings.ssh_alias
        if saved and saved not in values:
            values = (*values, saved)
        return {'schema_version': 1,
                'aliases': [{'alias': alias, 'saved': alias == saved} for alias in values]}

    async def inspect(self, alias: str, *, port: int | None = None) -> dict[str, object]:
        settings = self._settings()
        binary = self._binary(settings)
        if not binary:
            raise SshConnectionError('OpenSSH client is not installed.', status=503)
        try:
            result = await asyncio.to_thread(
                inspect_alias, alias, ssh=binary,
                ssh_keygen=shutil.which('ssh-keygen') or 'ssh-keygen',
                ssh_keyscan=shutil.which('ssh-keyscan') or 'ssh-keyscan',
                config_path=self._config(settings), **({'port': port} if port is not None else {}))
        except SshError as error:
            raise SshConnectionError(str(error), status=409 if error.code == 'SSH_HOST_KEY_UNKNOWN' else 400) from None
        return {'schema_version': 1, 'identity': result}

    async def test(self, alias: str, password: str = '', *, lease=None, port: int | None = None,
                   reuse_saved_credentials: bool = True) -> dict[str, object]:
        settings = self._settings()
        # Snapshot the owner and credential together before either awaited lookup.
        saved_lease = getattr(self.monitor, 'ssh_password_lease', None)
        reviewed = await self.inspect(alias, **({'port': port} if port is not None else {}))
        identity = reviewed['identity']
        if not identity['known']:
            raise SshConnectionError(
                'Host key is not in known_hosts. Verify the fingerprint independently, '
                'add it to your OpenSSH known_hosts, then test again.', status=409)
        if identity['changed']:
            raise SshConnectionError(
                'Host key changed. Odessy will not replace the trusted key. Compare the '
                'fingerprints and update your OpenSSH files deliberately.', status=409)
        host = await asyncio.to_thread(resolve_host, alias, ssh=self._binary(settings),
                                       config_path=self._config(settings), **({'port': port} if port is not None else {}))
        temporary = PasswordLease(password) if password else None
        try:
            credential = temporary or lease or (saved_lease
                if reuse_saved_credentials and settings.ssh_alias == alias and settings.ssh_port == (port or 0) else None)
            options = {}
            if credential is not None:
                helper = Path(settings.binary)
                if not helper.is_absolute() or not helper.is_file():
                    raise SshConnectionError('Password login needs the packaged monitoring helper.', status=503)
                options = dict(password_lease=credential, askpass=str(helper))
            result = await asyncio.to_thread(probe_connection, host,
                                             ssh=self._binary(settings), timeout=8, **options)
        except SshError as error:
            raise SshConnectionError(str(error), status=409) from None
        except ValueError:
            raise SshConnectionError('SSH password expired or is unavailable; enter it again.', status=409) from None
        finally:
            if temporary:
                temporary.close()
        return {'schema_version': 1, 'test': result, 'identity': identity}

    async def save(self, alias: str, display_name: str = '', password: str = '') -> dict[str, object]:
        async with self._lock:
            self.disable_execution()
            self._login_verified=False
            old = getattr(self.monitor, 'ssh_password_lease', None)
            settings = self._settings()
            candidate = PasswordLease(password) if password else (
                old if settings.ssh_alias == alias and settings.ssh_port == 0 else None)
            try:
                result = await self._coordinated(self._save(alias, display_name, candidate))
            except BaseException:
                # A cancelled waiter may still have completed the coordinated save.
                current = getattr(self.monitor, 'ssh_password_lease', None)
                if candidate is not old and current is not candidate and candidate:
                    candidate.close()
                if current is candidate and old is not candidate and old:
                    old.close()
                raise
            if old is not candidate and old:
                old.close()
            return result

    async def connect(self, target: str, *, port: int | None = None, password: str = '') -> dict[str, object]:
        """Connect only for this running service; never write target or credentials."""
        if port is not None and (type(port) is not int or not 1 <= port <= 65535):
            raise SshConnectionError('Port must be between 1 and 65535')
        async with self._lock:
            self.disable_execution()
            self._login_verified=False
            old = getattr(self.monitor, 'ssh_password_lease', None)
            candidate = PasswordLease(password) if password else None
            try:
                result = await self._coordinated(self._save(target, target, candidate,
                    temporary=True, port=port))
            finally:
                current = getattr(self.monitor, 'ssh_password_lease', None)
                if candidate is not None and current is not candidate:
                    candidate.close()
                if old is not None and current is not old:
                    old.close()
            return result

    async def _coordinated(self, operation):
        # Keep the lock until file writes and runtime transition have settled.
        task = asyncio.create_task(operation)
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def _save(self, alias: str, display_name: str, lease=None, *, temporary=False, port=None) -> dict[str, object]:
        settings = self._settings()
        if self.monitor is None:
            raise SshConnectionError('Monitoring service is unavailable.', status=503)
        try:
            _validate_alias(alias)
        except SshError as error:
            raise SshConnectionError(str(error), status=400) from None
        display_name = display_name.strip() or alias
        try:
            validate_env_value(display_name)
        except ValueError as error:
            raise SshConnectionError(f'Invalid display name: {error}', status=400) from None
        options = {'port': port} if port is not None else {}
        if temporary:
            options['reuse_saved_credentials'] = False
        result = await self.test(alias, lease=lease, **options) if lease is not None else await self.test(alias, **options)
        if not result['test'].get('ok'):
            if temporary:
                return {'schema_version': 1, 'connected': False, **result}
            raise SshConnectionError(str(result['test'].get('message') or 'SSH login failed'), status=409)
        if temporary and '@' in alias:
            hostname = result['test'].get('hostname', '')
            # Accept only bounded display metadata from a successful probe.
            import re
            if isinstance(hostname, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,252}', hostname):
                display_name = hostname
        host_id = self.host_id(f'{alias}:{port or result["identity"]["port"]}' if temporary else alias)
        hosts = tuple(value for value in settings.host_ids
                      if value not in {host_id, settings.ssh_host_id})
        if host_id not in hosts:
            hosts = (*hosts, host_id)
        ssh_settings = replace(
            settings, enabled=True, host_ids=hosts,
            ssh_alias=alias, ssh_host_id=host_id,
            ssh_display_name=display_name,
            ssh_monitoring=True, ssh_authentication='password' if lease else 'agent',
            ssh_port=port or 0, ssh_temporary=temporary,
        )
        values = {
            'ODESSY_SSH_COMPUTER': 'on',
            'ODESSY_SSH_MONITORING': 'on',
            'ODESSY_SSH_ALIAS': alias,
            'ODESSY_SSH_HOST_ID': host_id,
            'ODESSY_SSH_DISPLAY_NAME': display_name,
            'ODESSY_SSH_AUTHENTICATION': 'password' if lease else 'agent',
        }
        remove = ['ODESSY_SSH_CONFIG']
        if settings.ssh_config:
            values['ODESSY_SSH_CONFIG'] = settings.ssh_config
            remove = []
        path, previous = None, None
        if not temporary:
            path = self._config_file()
            try:
                previous = await asyncio.to_thread(read_env_text, path)
            except (OSError, ValueError) as error:
                raise SshConnectionError(f'Target could not be read: {error}', status=503) from None
            try:
                await asyncio.to_thread(update_env_values, path, values, remove=remove)
            except (OSError, ValueError) as error:
                raise SshConnectionError(f'Target could not be saved: {error}', status=503) from None
        old_lease = getattr(self.monitor, 'ssh_password_lease', None)
        self.monitor.ssh_password_lease = lease
        try:
            await self.monitor.reconfigure(ssh_settings)
            if getattr(self.monitor, 'ssh_error', ''):
                raise RuntimeError(self.monitor.ssh_error)
        except Exception as error:
            self.monitor.ssh_password_lease = old_lease
            restored = await self._rollback(path, previous, settings)
            suffix = ('The previous target was restored.' if restored else
                      'The previous target could not be fully restored; restart Odessy and verify it.')
            raise SshConnectionError(
                f'Monitoring did not start: {error}. {suffix}', status=503) from None
        self._verified_identity = dict(result['identity'])
        self._login_verified=True
        return {'schema_version': 1, 'connected': True, 'target': self.target()['target'],
                'identity': result['identity'], 'test': result['test']}

    async def remove(self) -> dict[str, object]:
        async with self._lock:
            return await self._coordinated(self._remove())

    async def _remove(self) -> dict[str, object]:
        settings = self._settings()
        old = settings.ssh_host_id
        hosts = tuple(value for value in settings.host_ids if value != old)
        enabled = bool(hosts) or settings.include_local or bool(settings.link_binary and Path(settings.link_binary).is_file())
        ssh_settings = replace(
            settings, enabled=enabled, host_ids=hosts,
            ssh_alias='', ssh_host_id='', ssh_display_name='',
            ssh_config='', ssh_monitoring=False, ssh_authentication='agent',
            ssh_port=0, ssh_temporary=False,
        )
        if settings.ssh_temporary:
            try:
                await self.monitor.reconfigure(ssh_settings)
            except Exception:
                await self.monitor.reconfigure(settings)
                raise SshConnectionError('Disconnect failed; the previous connection was restored.', status=503) from None
            self.close()
            return {'schema_version': 1, 'removed': True,
                    'message': 'Disconnected. The temporary password was forgotten. OS SSH access is unchanged.'}
        path = self._config_file()
        try:
            previous = await asyncio.to_thread(read_env_text, path)
        except (OSError, ValueError) as error:
            raise SshConnectionError(f'Target could not be read: {error}', status=503) from None
        try:
            await asyncio.to_thread(
                update_env_values, path, {},
                remove=['ODESSY_SSH_COMPUTER', 'ODESSY_SSH_MONITORING',
                        'ODESSY_SSH_ALIAS', 'ODESSY_SSH_HOST_ID',
                        'ODESSY_SSH_DISPLAY_NAME', 'ODESSY_SSH_CONFIG', 'ODESSY_SSH_AUTHENTICATION'])
        except (OSError, ValueError) as error:
            raise SshConnectionError(f'Target could not be removed: {error}', status=503) from None
        if self.monitor is not None:
            try:
                await self.monitor.reconfigure(ssh_settings)
            except Exception as error:
                restored = await self._rollback(path, previous, settings)
                suffix = ('The previous target was restored.' if restored else
                          'The previous target could not be fully restored; restart Odessy and verify it.')
                raise SshConnectionError(
                    f'Monitoring did not stop: {error}. {suffix}', status=503) from None
        self.close()
        return {'schema_version': 1,
                'removed': True,
                'message': 'Removed from Odessy. This did not revoke your SSH key or '
                           'authorized_keys access on the host; remove those separately if needed.'}

    def close(self):
        self.disable_execution()
        self._login_verified=False
        self._verified_identity=None
        lease = getattr(self.monitor, 'ssh_password_lease', None)
        if lease:
            self.monitor.ssh_password_lease = None
            lease.close()

    async def _rollback(self, path, previous, settings) -> bool:
        """Best-effort restore after a runtime change failed; never claim saved."""

        file_ok = True
        runtime_ok = True
        if path is not None:
            try:
                await asyncio.to_thread(restore_env_text, path, previous)
            except Exception:
                file_ok = False
        try:
            await self.monitor.reconfigure(settings)
        except Exception:
            runtime_ok = False
        return file_ok and runtime_ok


def register(app, monitor):
    from fastapi import HTTPException, Request

    manager = SshConnections(monitor)
    app.state.ssh_connections = manager

    async def body(request: Request) -> dict:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 8192:
                raise HTTPException(400, 'Request is too large')
        import json
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, 'Invalid JSON') from None
        if not isinstance(value, dict):
            raise HTTPException(400, 'Invalid request')
        return value

    def failure(error: SshConnectionError):
        raise HTTPException(error.status, str(error))

    @app.get('/v1/ssh/execution')
    async def ssh_execution_status():return manager.execution_status()

    def idle():
        if any(c.running for c in app.state.sessions.values()):
            raise HTTPException(409,'Stop active turns before changing the SSH execution target')

    @app.post('/v1/ssh/execution')
    async def ssh_enable_execution(request: Request):
        data=await body(request)
        if set(data) != {'confirmed'} or data.get('confirmed') is not True:
            raise HTTPException(400,'Confirm remote account execution without a sandbox')
        idle()
        try:
            result = await manager.enable_execution()
            try:
                idle()
            except HTTPException:
                manager.disable_execution()
                raise
            return result
        except SshConnectionError as error:failure(error)

    @app.delete('/v1/ssh/execution')
    async def ssh_disable_execution():
        return manager.disable_execution()

    @app.get('/v1/ssh/aliases')
    async def ssh_aliases():
        try:
            return await manager.aliases()
        except SshConnectionError as error:
            failure(error)

    @app.get('/v1/ssh/target')
    async def ssh_target():
        try:
            return manager.target()
        except SshConnectionError as error:
            failure(error)

    @app.post('/v1/ssh/inspect')
    async def ssh_inspect(request: Request):
        data = await body(request)
        if set(data) != {'alias'} or not isinstance(data['alias'], str) or not data['alias']:
            raise HTTPException(400, 'Choose one SSH alias')
        try:
            return await manager.inspect(data['alias'])
        except SshConnectionError as error:
            failure(error)

    @app.post('/v1/ssh/test')
    async def ssh_test(request: Request):
        data = await body(request)
        if set(data) - {'alias', 'password'} or not isinstance(data.get('alias'), str) or not data['alias']:
            raise HTTPException(400, 'Enter an SSH target')
        password = data.get('password', '')
        if not isinstance(password, str) or len(password) > 1024 or any(c in password for c in '\r\n\x00'):
            raise HTTPException(400, 'Invalid password')
        try:
            return await manager.test(data['alias'], password)
        except SshConnectionError as error:
            failure(error)

    @app.post('/v1/ssh/connect')
    async def ssh_connect(request: Request):
        data = await body(request)
        if set(data) - {'target', 'port', 'password'} or not isinstance(data.get('target'), str) or not data['target']:
            raise HTTPException(400, 'Enter user@hostname or an SSH alias')
        port = data.get('port')
        if port is not None and (type(port) is not int or not 1 <= port <= 65535):
            raise HTTPException(400, 'Port must be between 1 and 65535')
        password = data.get('password', '')
        if not isinstance(password, str) or len(password) > 1024 or any(c in password for c in '\r\n\x00'):
            raise HTTPException(400, 'Invalid password')
        try:
            return await manager.connect(data['target'], port=port, password=password)
        except SshConnectionError as error:
            failure(error)

    @app.post('/v1/ssh/target')
    async def ssh_save(request: Request):
        data = await body(request)
        if set(data) - {'alias', 'display_name', 'password'} or not isinstance(data.get('alias'), str) or not data['alias']:
            raise HTTPException(400, 'Choose one SSH alias')
        display = data.get('display_name', '')
        if not isinstance(display, str) or len(display) > 200:
            raise HTTPException(400, 'Invalid display name')
        password = data.get('password', '')
        if not isinstance(password, str) or len(password) > 1024 or any(c in password for c in '\r\n\x00'):
            raise HTTPException(400, 'Invalid password')
        try:
            return await manager.save(data['alias'], display, password)
        except SshConnectionError as error:
            failure(error)

    @app.delete('/v1/ssh/target')
    async def ssh_remove():
        try:
            return await manager.remove()
        except SshConnectionError as error:
            failure(error)
