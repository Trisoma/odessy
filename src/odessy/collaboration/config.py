"""Validated named participant profiles, stored outside the project workspace."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit
try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib

from odessy.models.usage import Pricing

CONFIG_ENV = 'ODESSY_COLLABORATION_FILE'
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
_ADAPTERS = {'cloud', 'ollama', 'lfm', 'codex', 'claude_code', 'custom_agent', 'auto'}


def _text(value, name, limit=4096):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise ValueError(f'{name} must be a string of at most {limit} characters')


def _count(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer between {low} and {high}')


def _strings(value, name, allowed=None):
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise ValueError(f'{name} must be an array of strings')
    if len(value) > 128 or any(len(item) > 4096 or '\x00' in item for item in value):
        raise ValueError(f'{name} is too large')
    if allowed is not None and set(value) - allowed:
        raise ValueError(f'unsupported {name}')
    return tuple(value)


@dataclass(frozen=True)
class ParticipantProfile:
    name: str
    adapter: str
    model: str = ''
    provider: str = ''
    endpoint: str = ''
    credential_env: str = ''
    host: str = 'local'
    capabilities: tuple[str, ...] = ('consult',)
    command: tuple[str, ...] = ()
    workspace: str = ''
    allowed_modes: tuple[str, ...] = ('advice',)
    pricing: Pricing | None = None
    timeout: int = 120
    max_tokens: int = 4096

    def __post_init__(self):
        if not isinstance(self.name, str) or not _NAME.fullmatch(self.name):
            raise ValueError('invalid participant name')
        if self.adapter not in _ADAPTERS:
            raise ValueError('unsupported participant adapter')
        for name in ('model', 'provider', 'endpoint', 'credential_env', 'host', 'workspace'):
            _text(getattr(self, name), name)
        if self.credential_env and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', self.credential_env):
            raise ValueError('credential_env must reference an environment variable')
        if self.endpoint:
            endpoint = urlsplit(self.endpoint)
            if (endpoint.scheme not in {'http', 'https'} or not endpoint.hostname
                    or endpoint.username is not None or endpoint.password is not None
                    or endpoint.query or endpoint.fragment):
                raise ValueError('endpoint must be an HTTP URL without credentials, query, or fragment')
        if not self.host.strip():
            raise ValueError('host must not be empty')
        for name in ('capabilities', 'command', 'allowed_modes'):
            values = _strings(getattr(self, name), name)
            if name != 'command' and any(not _NAME.fullmatch(value) for value in values):
                raise ValueError(f'invalid {name} name')
            object.__setattr__(self, name, values)
        _count(self.timeout, 'timeout', 1, 3600)
        _count(self.max_tokens, 'max_tokens', 1, 1_000_000)
        pricing = self.pricing
        if isinstance(pricing, Mapping):
            pricing = Pricing(**pricing)
            object.__setattr__(self, 'pricing', pricing)
        if pricing is not None:
            if not isinstance(pricing, Pricing):
                raise ValueError('pricing must be a pricing table')
            for value in (pricing.input, pricing.cached_input, pricing.output):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError('prices must be finite nonnegative numbers')
            _text(pricing.currency, 'currency', 32)

    def public_dict(self):
        return {key: value for key, value in asdict(self).items() if value is not None}

    @property
    def effective_credential_env(self):
        if self.credential_env:
            return self.credential_env
        if self.adapter == 'cloud':
            from odessy.models.cloud_model import CLOUD_PROVIDERS
            provider = CLOUD_PROVIDERS.get(self.provider or 'openai')
            return provider.api_key_env if provider is not None else ''
        return ''


@dataclass(frozen=True)
class CollaborationSettings:
    enabled: bool = False
    automatic: bool = False
    default_participant: str = ''
    max_consultations: int = 1
    max_context_chars: int = 8000
    max_rounds: int = 1
    rules: tuple = ()

    def __post_init__(self):
        if type(self.enabled) is not bool or type(self.automatic) is not bool:
            raise ValueError('enabled and automatic must be booleans')
        if not isinstance(self.default_participant, str) or (self.default_participant and not _NAME.fullmatch(self.default_participant)):
            raise ValueError('invalid default participant')
        _count(self.max_consultations, 'max_consultations', 0, 32)
        _count(self.max_context_chars, 'max_context_chars', 1, 100_000)
        _count(self.max_rounds, 'max_rounds', 0, 32)
        if not isinstance(self.rules, (tuple, list)) or len(self.rules) > 128:
            raise ValueError('rules must be an array with at most 128 entries')
        normalized = []
        for rule in self.rules:
            if not isinstance(rule, Mapping) or set(rule) - {'pattern', 'action', 'participant'}:
                raise ValueError('invalid collaboration rule')
            pattern = rule.get('pattern', '')
            _text(pattern, 'rule pattern', 1024)
            if rule.get('action') not in ('allow', 'deny'):
                raise ValueError('rule action must be allow or deny')
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError(f'invalid rule pattern: {error}') from error
            target = rule.get('participant', '')
            if not isinstance(target, str) or (target and not _NAME.fullmatch(target)):
                raise ValueError('invalid rule participant')
            normalized.append(dict(rule))
        object.__setattr__(self, 'rules', tuple(normalized))


@dataclass(frozen=True)
class CollaborationConfig:
    settings: CollaborationSettings = field(default_factory=CollaborationSettings)
    participants: dict[str, ParticipantProfile] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.settings, CollaborationSettings) or not isinstance(self.participants, dict):
            raise ValueError('invalid collaboration configuration')
        if len(self.participants) > 128:
            raise ValueError('at most 128 participants may be configured')
        for name, profile in self.participants.items():
            if name == 'current':
                raise ValueError("Participant name 'current' is reserved for the initial coordinator")
            if not isinstance(profile, ParticipantProfile) or name != profile.name:
                raise ValueError('participant table name must match profile name')

    def public_dict(self):
        return {'settings': asdict(self.settings),
                'participants': {name: profile.public_dict() for name, profile in self.participants.items()}}


def _config(data):
    if isinstance(data, CollaborationConfig):
        data = data.public_dict()
    if not isinstance(data, Mapping) or set(data) - {'settings', 'participants'}:
        raise ValueError('configuration must contain settings and participants only')
    settings = data.get('settings', {})
    participants = data.get('participants', {})
    if not isinstance(settings, Mapping) or not isinstance(participants, Mapping):
        raise ValueError('settings and participants must be tables')
    try:
        profiles = {}
        for name, values in participants.items():
            if not isinstance(values, Mapping):
                raise ValueError('participant must be a table')
            values = dict(values)
            if 'name' in values and values['name'] != name:
                raise ValueError('participant name mismatch')
            values['name'] = name
            profiles[name] = ParticipantProfile(**values)
        return CollaborationConfig(CollaborationSettings(**settings), profiles)
    except (TypeError, KeyError) as error:
        raise ValueError(f'invalid collaboration configuration: {error}') from error


def _path(path, environ):
    env = os.environ if environ is None else environ
    return Path(path or env.get(CONFIG_ENV) or Path.home() / '.config' / 'odessy' / 'collaboration.toml').expanduser()


def load_config(path=None, environ=None):
    target = _path(path, environ)
    if not target.exists():
        return CollaborationConfig()
    try:
        with target.open('rb') as handle:
            return _config(tomllib.load(handle))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f'invalid collaboration TOML: {error}') from error


def _toml_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (tuple, list)):
        return '[' + ', '.join(_toml_value(item) for item in value) + ']'
    if isinstance(value, Mapping):
        return '{' + ', '.join(f'{json.dumps(key)} = {_toml_value(item)}' for key, item in value.items()) + '}'
    raise ValueError('unsupported TOML value')


def save_config(data, path=None, environ=None):
    config = _config(data)
    lines = ['[settings]']
    lines.extend(f'{key} = {_toml_value(value)}' for key, value in asdict(config.settings).items())
    for name, profile in config.participants.items():
        lines += ['', f'[participants.{json.dumps(name)}]']
        lines.extend(f'{key} = {_toml_value(value)}' for key, value in profile.public_dict().items() if key != 'name')
    target = _path(path, environ)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=target.parent, prefix='.' + target.name, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write('\n'.join(lines) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return config
