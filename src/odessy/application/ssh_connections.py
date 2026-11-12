"""Application-level actions for one saved SSH computer.

Polling, freshness, events and host projection belong to the Go monitoring service
(prompt 18). This module only lists/reviews aliases, runs one bounded connection
test, writes the selected target to the existing config file, and asks the existing
supervisor to reconfigure. It never edits known_hosts or private key files.
"""

import asyncio
import hashlib
import os
import re
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
)

_ALIAS = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')


class SshConnectionError(ValueError):
    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


class SshConnections:
    def __init__(self, monitor):
        self.monitor = monitor
        self._lock = asyncio.Lock()

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

    async def inspect(self, alias: str) -> dict[str, object]:
        settings = self._settings()
        binary = self._binary(settings)
        if not binary:
            raise SshConnectionError('OpenSSH client is not installed.', status=503)
        try:
            result = await asyncio.to_thread(
                inspect_alias, alias, ssh=binary,
                ssh_keygen=shutil.which('ssh-keygen') or 'ssh-keygen',
                ssh_keyscan=shutil.which('ssh-keyscan') or 'ssh-keyscan',
                config_path=self._config(settings))
        except SshError as error:
            raise SshConnectionError(str(error), status=409 if error.code == 'SSH_HOST_KEY_UNKNOWN' else 400) from None
        return {'schema_version': 1, 'identity': result}

    async def test(self, alias: str) -> dict[str, object]:
        settings = self._settings()
        reviewed = await self.inspect(alias)
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
                                       config_path=self._config(settings))
        result = await asyncio.to_thread(probe_connection, host,
                                         ssh=self._binary(settings), timeout=8)
        if not result.get('ok'):
            raise SshConnectionError(
                str(result.get('message') or 'SSH login failed'),
                status=503 if result.get('code') == 'SSH_TIMEOUT' else 409)
        return {'schema_version': 1, 'test': result, 'identity': identity}

    async def save(self, alias: str, display_name: str = '') -> dict[str, object]:
        async with self._lock:
            return await self._save(alias, display_name)

    async def _save(self, alias: str, display_name: str) -> dict[str, object]:
        settings = self._settings()
        if self.monitor is None:
            raise SshConnectionError('Monitoring service is unavailable.', status=503)
        if not _ALIAS.fullmatch(alias):
            raise SshConnectionError('Choose a valid OpenSSH alias', status=400)
        display_name = display_name.strip() or alias
        try:
            validate_env_value(display_name)
        except ValueError as error:
            raise SshConnectionError(f'Invalid display name: {error}', status=400) from None
        result = await self.test(alias)
        host_id = self.host_id(alias)
        hosts = tuple(value for value in settings.host_ids if value != host_id)
        if host_id not in hosts:
            hosts = (*hosts, host_id)
        ssh_settings = replace(
            settings, enabled=True, host_ids=hosts,
            ssh_alias=alias, ssh_host_id=host_id,
            ssh_display_name=display_name,
            ssh_monitoring=True,
        )
        values = {
            'ODESSY_SSH_COMPUTER': 'on',
            'ODESSY_SSH_MONITORING': 'on',
            'ODESSY_SSH_ALIAS': alias,
            'ODESSY_SSH_HOST_ID': host_id,
            'ODESSY_SSH_DISPLAY_NAME': display_name,
        }
        remove = ['ODESSY_SSH_CONFIG']
        if settings.ssh_config:
            values['ODESSY_SSH_CONFIG'] = settings.ssh_config
            remove = []
        path = self._config_file()
        try:
            previous = await asyncio.to_thread(read_env_text, path)
        except (OSError, ValueError) as error:
            raise SshConnectionError(f'Target could not be read: {error}', status=503) from None
        try:
            await asyncio.to_thread(update_env_values, path, values, remove=remove)
        except (OSError, ValueError) as error:
            raise SshConnectionError(f'Target could not be saved: {error}', status=503) from None
        try:
            await self.monitor.reconfigure(ssh_settings)
            if getattr(self.monitor, 'ssh_error', ''):
                raise RuntimeError(self.monitor.ssh_error)
        except Exception as error:
            restored = await self._rollback(path, previous, settings)
            suffix = ('The previous target was restored.' if restored else
                      'The previous target could not be fully restored; restart Odessy and verify it.')
            raise SshConnectionError(
                f'Monitoring did not start: {error}. {suffix}', status=503) from None
        return {'schema_version': 1, 'target': self.target()['target'], 'test': result['test']}

    async def remove(self) -> dict[str, object]:
        async with self._lock:
            return await self._remove()

    async def _remove(self) -> dict[str, object]:
        settings = self._settings()
        old = settings.ssh_host_id
        hosts = tuple(value for value in settings.host_ids if value != old)
        enabled = bool(hosts) or settings.include_local or bool(settings.link_binary)
        ssh_settings = replace(
            settings, enabled=enabled, host_ids=hosts,
            ssh_alias='', ssh_host_id='', ssh_display_name='',
            ssh_config='', ssh_monitoring=False,
        )
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
                        'ODESSY_SSH_DISPLAY_NAME', 'ODESSY_SSH_CONFIG'])
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
        return {'schema_version': 1,
                'removed': True,
                'message': 'Removed from Odessy. This did not revoke your SSH key or '
                           'authorized_keys access on the host; remove those separately if needed.'}

    async def _rollback(self, path, previous, settings) -> bool:
        """Best-effort restore after a runtime change failed; never claim saved."""

        file_ok = True
        runtime_ok = True
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
        if set(data) != {'alias'} or not isinstance(data['alias'], str) or not data['alias']:
            raise HTTPException(400, 'Choose one SSH alias')
        try:
            return await manager.test(data['alias'])
        except SshConnectionError as error:
            failure(error)

    @app.post('/v1/ssh/target')
    async def ssh_save(request: Request):
        data = await body(request)
        if set(data) - {'alias', 'display_name'} or not isinstance(data.get('alias'), str) or not data['alias']:
            raise HTTPException(400, 'Choose one SSH alias')
        display = data.get('display_name', '')
        if not isinstance(display, str) or len(display) > 200:
            raise HTTPException(400, 'Invalid display name')
        try:
            return await manager.save(data['alias'], display)
        except SshConnectionError as error:
            failure(error)

    @app.delete('/v1/ssh/target')
    async def ssh_remove():
        try:
            return await manager.remove()
        except SshConnectionError as error:
            failure(error)
