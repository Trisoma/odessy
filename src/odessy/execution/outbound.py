"""Explicit coordinator-owned outbound consent; never inferred from inbound grants.

The record is public configuration, not a credential. Writers use the existing
settings writer lock, exact review and atomic publication. Rust remains the source
of certificate identity/revocation and the receiver's independent admission policy.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

OWNER = 'odessy-outbound-consent-v1'
_ALIAS = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
_HASH = re.compile(r'[0-9a-f]{64}')


def config_path() -> Path:
    from odessy.setup.configured import desktop_state_file
    return desktop_state_file().parent / 'execution-consent.json'


def trust_scope() -> str:
    selected = os.environ.get('ODESSY_LINK_STATE_DIR', '').strip()
    return str(Path(selected).expanduser().resolve()) if selected else 'current-user-default'


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read() -> dict:
    path = config_path()
    value = dict(owner=OWNER, schema_version=1, alias='', fingerprint_sha256='',
                 enabled=False, trust_scope=trust_scope(), generation='0' * 32)
    if path.is_symlink():
        raise ValueError('Execution consent must not be a symbolic link; inspect configuration.')
    if path.exists():
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
                    raise ValueError
                if hasattr(os, 'getuid') and info.st_uid != os.getuid():
                    raise ValueError
                raw = stream.read(16385)
            value = json.loads(raw)
            if (not isinstance(value, dict) or set(value) != {'owner', 'schema_version', 'alias',
                    'fingerprint_sha256', 'enabled', 'trust_scope', 'generation'} or
                    value['owner'] != OWNER or type(value['schema_version']) is not int or value['schema_version'] != 1 or
                    type(value['enabled']) is not bool or not isinstance(value['alias'], str) or
                    not _ALIAS.fullmatch(value['alias']) or not isinstance(value['fingerprint_sha256'], str) or
                    not _HASH.fullmatch(value['fingerprint_sha256']) or not isinstance(value['trust_scope'], str) or
                    len(value['trust_scope']) > 4096 or not isinstance(value['generation'], str) or
                    not re.fullmatch('[0-9a-f]{32}', value['generation'])):
                raise ValueError
        except (OSError, ValueError, TypeError, KeyError):
            raise ValueError('Execution consent is unreadable or unowned; inspect configuration without overwriting it.') from None
    return dict(value, revision=_digest(value))


def authorize(alias: str, fingerprint: str) -> str:
    try:
        value = read()
    except ValueError as error:
        raise PermissionError(str(error)) from error
    if (not value['enabled'] or value['alias'] != alias or value['fingerprint_sha256'] != fingerprint or
            value['trust_scope'] != trust_scope()):
        raise PermissionError('Explicit outbound execution consent is required. Review the execution target in Devices.')
    return value['revision']


def selected_alias() -> str:
    """Keep a disabled/unreadable selected target from falling back to local tools."""
    try:
        return read()['alias']
    except ValueError:
        return 'consent-unavailable'


def apply_environment(environment) -> None:
    alias = selected_alias()
    if alias:
        environment['ODESSY_PAIRED_HOST'] = alias
        environment['ODESSY_DEPLOYMENT'] = 'split'
        # An explicitly selected paired target replaces the legacy URL transport.
        environment.pop('ODESSY_MCP_SERVER_URLS', None)
        environment.pop('ODESSY_TOOL_SERVER_URL', None)


def review(alias: str, enabled: bool, binary: Path | None, *, prober=None) -> dict:
    if not isinstance(alias, str) or not _ALIAS.fullmatch(alias) or type(enabled) is not bool:
        raise ValueError('Choose a valid device and explicit execution consent.')
    current = read()
    fingerprint = current['fingerprint_sha256']
    registry_revision = None
    if enabled:
        from odessy.tools.paired import PairedHost, probe
        if binary is None or not binary.is_file():
            raise ValueError('The verified connection component is unavailable.')
        details = (prober or probe)(PairedHost(alias, binary))
        fingerprint = details.get('fingerprint_sha256')
        registry_revision = details.get('grant_revision')
        if (details.get('ok') is not True or details.get('configured_alias_present') is not True or
                details.get('revoked') is not False or not isinstance(fingerprint, str) or
                not _HASH.fullmatch(fingerprint) or type(registry_revision) is not int):
            raise ValueError('Choose a currently enrolled, non-revoked identity. Discovery is not enrollment.')
    elif alias != current['alias']:
        raise ValueError('Disable the selected execution target, not a different device.')
    value = dict(alias=alias, fingerprint_sha256=fingerprint, enabled=enabled,
                 trust_scope=trust_scope(), revision=current['revision'], registry_revision=registry_revision,
                 binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest() if binary and binary.is_file() else None)
    return dict(value, plan_id=_digest(value), restart_required=True,
                message='Use this verified device for outbound tools. No incoming capability or workspace grant is created.' if enabled else
                'Disable outbound execution. The selected target remains configured; chat continues without a local fallback.')


def save(alias: str, enabled: bool, binary: Path | None, revision: str, confirm: str, *, prober=None) -> dict:
    candidate = review(alias, enabled, binary, prober=prober)
    if candidate['revision'] != revision or candidate['plan_id'] != confirm:
        raise ValueError('Execution target or state changed. Review again before saving.')
    from odessy.setup.component_lock import writer_lock
    with writer_lock():
        candidate = review(alias, enabled, binary, prober=prober)
        if candidate['revision'] != revision or candidate['plan_id'] != confirm:
            raise ValueError('Execution target changed during review. Inspect before retrying.')
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        value = {key: candidate[key] for key in ('alias', 'fingerprint_sha256', 'enabled', 'trust_scope')}
        value.update(owner=OWNER, schema_version=1, generation=uuid.uuid4().hex)
        fd, temporary = tempfile.mkstemp(prefix='.execution-consent-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            if path.is_symlink():
                raise ValueError('Execution consent ownership changed; inspect before retrying.')
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return dict(read(), saved=True, restart_required=True)
