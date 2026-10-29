"""Local model credentials. Values never belong in settings or tool environments.

OS storage protects against routine disclosure, not arbitrary same-user code.
Only the fixed stdio bridge may manage values; there is no HTTP secret API.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import sys
import threading
from urllib.parse import urlsplit

SERVICE = 'Odessy/model-credentials/v1'
_lock = threading.RLock()
_sessions: dict[str, str] = {}
_references: dict[tuple[str, str], str] = {}


class CredentialError(RuntimeError):
    def __init__(self, message, *, code='credentials'):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Scope:
    provider: str
    endpoint: str = ''
    credential_env: str = ''

    def __post_init__(self):
        from odessy.model_providers import CLOUD_PROVIDERS
        if self.provider not in CLOUD_PROVIDERS:
            raise CredentialError('Choose a supported cloud provider.')
        if not isinstance(self.credential_env, str) or (self.credential_env and not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', self.credential_env)):
            raise CredentialError('Invalid credential reference.')
        try:
            endpoint = self.endpoint or CLOUD_PROVIDERS[self.provider].base_url
            parsed = urlsplit(endpoint)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError()
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            if not 1 <= port <= 65535:
                raise ValueError()
            hostname = parsed.hostname.lower()
            if ':' in hostname:
                hostname = '[' + hostname + ']'
            origin = f'{parsed.scheme}://{hostname}:{port}'
        except (ValueError, TypeError, AttributeError):
            raise CredentialError('Choose a valid provider endpoint without URL credentials.') from None
        object.__setattr__(self, 'endpoint', origin)

    @property
    def identity(self):
        return hashlib.sha256(json.dumps([self.provider, self.endpoint, self.credential_env], separators=(',', ':')).encode()).hexdigest()


def approved_backend(backend):
    # Exact approved native implementations, never a file backend or Chainer.
    try:
        if sys.platform == 'win32':
            from keyring.backends.Windows import WinVaultKeyring
            return type(backend) is WinVaultKeyring
        if sys.platform.startswith('linux'):
            from keyring.backends.SecretService import Keyring
            return type(backend) is Keyring
    except ImportError:
        pass
    return False


def _backend():
    try:
        import keyring
        backend = keyring.get_keyring()
        if approved_backend(backend):
            return backend
    except Exception:
        pass
    raise CredentialError('Secure storage is unavailable or unsupported. Use this session only, or unlock/configure the OS credential service.', code='unavailable')


def _environment_value(scope, environ):
    from odessy.model_providers import CLOUD_PROVIDERS
    names = [scope.credential_env] if scope.credential_env else ['ODESSY_CLOUD_API_KEY', CLOUD_PROVIDERS[scope.provider].api_key_env]
    return next((environ[name].strip() for name in names if environ.get(name, '').strip()), None)


def model_scope(provider, endpoint='', credential_env=''):
    scope = Scope(provider, endpoint, credential_env)
    if not credential_env:
        reference = _references.get((provider, scope.endpoint), '')
        if reference:
            scope = Scope(provider, endpoint, reference)
    return scope


def configure_model(model):
    scope = Scope(model.provider, model.endpoint, model.credential_env)
    with _lock:
        _references[(scope.provider, scope.endpoint)] = model.credential_env


def resolve(scope, *, environ=None):
    env = os.environ if environ is None else environ
    value = _environment_value(scope, env)
    if value:
        return value
    with _lock:
        value = _sessions.get(scope.identity)
    if value:
        return value
    try:
        value = _backend().get_password(SERVICE, scope.identity)
        return value.strip() if isinstance(value, str) and value.strip() else None
    except Exception:
        return None


def describe(scope, *, environ=None):
    env = os.environ if environ is None else environ
    source, available, message = 'none', True, ''
    stored = None
    try:
        stored = _backend().get_password(SERVICE, scope.identity)
    except Exception:
        available = False
        message = 'Secure storage is unavailable or locked. You can use this session only.'
    if _environment_value(scope, env):
        source = 'env'
    elif _sessions.get(scope.identity):
        source = 'session'
    elif isinstance(stored, str) and stored.strip():
        source = 'keyring'
    return {'scope': scope.identity, 'configured': source != 'none', 'source': source,
            'writable': source != 'env', 'persistence': 'persistent' if source == 'keyring' else 'session' if source == 'session' else 'none',
            'secure_storage_available': available, 'message': message}


def _writable(scope, storage, environ):
    if storage not in ('keyring', 'session'):
        raise CredentialError('Choose secure storage or this session only.')
    if _environment_value(scope, os.environ if environ is None else environ):
        raise CredentialError('This credential comes from the process environment and is read-only. Remove that environment value and reopen Odessy to manage it here.')


def set_value(scope, value, *, storage, environ=None):
    _writable(scope, storage, environ)
    if not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > 16384 or any(c in value for c in '\r\n\x00'):
        raise CredentialError('Enter a nonempty API key of at most 16 KiB, without line breaks.')
    value = value.strip()
    if storage == 'keyring':
        try:
            backend = _backend()
            backend.set_password(SERVICE, scope.identity, value)
            if backend.get_password(SERVICE, scope.identity) != value:
                raise CredentialError('Secure save could not be confirmed.')
        except Exception:
            raise CredentialError('Secure save failed. Unlock the OS credential service or choose this session only.', code='unavailable') from None
        with _lock:
            _sessions.pop(scope.identity, None)
    else:
        with _lock:
            _sessions[scope.identity] = value
    return describe(scope, environ=environ)


def remove(scope, *, storage, environ=None):
    _writable(scope, storage, environ)
    if storage == 'keyring':
        try:
            backend = _backend()
            if backend.get_password(SERVICE, scope.identity) is not None:
                backend.delete_password(SERVICE, scope.identity)
        except Exception:
            raise CredentialError('Secure removal failed. Unlock the OS credential service and inspect status before retrying.', code='unavailable') from None
    with _lock:
        _sessions.pop(scope.identity, None)
    return describe(scope, environ=environ)


def clear_sessions():
    with _lock:
        _sessions.clear()
        _references.clear()


def load_sessions(entries):
    if not isinstance(entries, list) or len(entries) > 32:
        raise CredentialError('Invalid session credential envelope.')
    values = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {'provider', 'endpoint', 'credential_env', 'value'}:
            raise CredentialError('Invalid session credential scope.')
        scope = Scope(entry.get('provider'), entry.get('endpoint', ''), entry.get('credential_env', ''))
        value = entry.get('value')
        if not isinstance(value, str) or not value.strip() or len(value.encode()) > 16384 or any(c in value for c in '\r\n\x00'):
            raise CredentialError('Invalid session credential value.')
        values[scope.identity] = value.strip()
    with _lock:
        _sessions.clear()
        _sessions.update(values)


def read_startup(stdin):
    raw = stdin.readline(65537)
    try:
        if len(raw) > 65536 or not raw.endswith(b'\n'):
            raise ValueError()
        update = json.loads(raw)
        if set(update) not in ({'session_credentials'}, {'session_credentials', 'credential_revision'}):
            raise ValueError()
        revision = update.get('credential_revision')
        if revision is not None and (type(revision) is not int or not 0 <= revision < 2**53):
            raise ValueError()
        load_sessions(update['session_credentials'])
        return revision
    except (ValueError, TypeError, AttributeError):
        raise CredentialError('Invalid private credential startup message.') from None


def acknowledge_startup(stdin, stdout):
    revision = read_startup(stdin)
    if revision is not None:
        stdout.write(json.dumps({'credential_revision': revision}) + '\n')
        stdout.flush()


def dispatch(parameters):
    allowed = {'action', 'provider', 'endpoint', 'credential_env', 'storage', 'value'}
    if not isinstance(parameters, dict) or set(parameters) - allowed:
        raise CredentialError('Invalid credential operation.')
    scope = Scope(parameters.get('provider'), parameters.get('endpoint', ''), parameters.get('credential_env', ''))
    action = parameters.get('action')
    if action == 'describe' and not {'value', 'storage'} & parameters.keys():
        return describe(scope)
    if action == 'set':
        return set_value(scope, parameters.get('value'), storage=parameters.get('storage'))
    if action == 'remove' and 'value' not in parameters:
        return remove(scope, storage=parameters.get('storage'))
    raise CredentialError('Unsupported credential operation.')


def tool_environment(source):
    """Preserve execution configuration, but never ambient model credentials."""
    from odessy.model_providers import CLOUD_PROVIDERS
    names = {p.api_key_env.upper() for p in CLOUD_PROVIDERS.values()} | {'ODESSY_CLOUD_API_KEY', 'ODESSY_API_TOKEN'}
    names.update(ref.upper() for ref in _references.values() if ref)
    return {key: value for key, value in source.items()
            if key.upper() not in names and not re.search(r'KEY|PASSWORD|SECRET|TOKEN', key, re.I)}
