"""Versioned setup contracts shared by headless and future desktop frontends."""
from __future__ import annotations

import math
import platform
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
ROLES = ('coordinator', 'execution', 'combined')
CLOUD_PROVIDERS = ('claude', 'openai', 'deepseek', 'qwen', 'kimi', 'custom')


def text(value: object, name: str, *, optional: bool = False) -> str:
    if not isinstance(value, str) or (not optional and not value.strip()):
        raise ValueError(f'{name} must be a nonempty string')
    if len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value) or '$' in value:
        raise ValueError(f'{name} contains unsupported control or expansion characters')
    return value.strip()


def endpoint_url(value: object) -> str:
    value = text(value, 'endpoint')
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or (port is not None and port == 0)
                or any(c.isspace() for c in value) or '\\' in value):
            raise ValueError
    except ValueError:
        raise ValueError('endpoint must be HTTP(S) with a hostname, valid port and no credentials/query/fragment') from None
    return value.rstrip('/')


def fields(data: object, allowed: set[str], name: str) -> dict:
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError(f'{name} must be an object containing only supported fields')
    return data


@dataclass(frozen=True)
class ModelSelection:
    provider: str
    name: str
    endpoint: str = ''
    credential_env: str = ''

    def __post_init__(self):
        if self.provider not in ('ollama', 'lfm', *CLOUD_PROVIDERS):
            raise ValueError('unsupported model provider; configure one model before enabling Auto')
        object.__setattr__(self, 'name', text(self.name, 'model name'))
        object.__setattr__(self, 'endpoint', text(self.endpoint, 'endpoint', optional=True))
        object.__setattr__(self, 'credential_env', text(self.credential_env, 'credential_env', optional=True))
        if self.endpoint:
            object.__setattr__(self, 'endpoint', endpoint_url(self.endpoint))
        elif self.provider in ('ollama', 'lfm'):
            object.__setattr__(self, 'endpoint', 'http://127.0.0.1:11434')
        if self.credential_env and not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', self.credential_env):
            raise ValueError('credential_env must name an environment variable, not contain a secret')
        if self.provider == 'custom' and not self.endpoint:
            raise ValueError('custom provider requires an endpoint')

    @classmethod
    def from_dict(cls, data: object) -> ModelSelection:
        try:
            return cls(**fields(data, {'provider', 'name', 'endpoint', 'credential_env'}, 'model'))
        except TypeError:
            raise ValueError('model requires a supported provider and explicit name') from None


@dataclass(frozen=True)
class VoiceSelection:
    enabled: bool = False
    whisper_bin: str = ''
    whisper_model: str = ''
    kokoro_model: str = ''
    kokoro_voices: str = ''

    def __post_init__(self):
        if not isinstance(self.enabled, bool):
            raise ValueError('voice.enabled must be a boolean')
        for name in ('whisper_bin', 'whisper_model', 'kokoro_model', 'kokoro_voices'):
            object.__setattr__(self, name, text(getattr(self, name), name, optional=True))
        if not self.enabled and any((self.whisper_bin, self.whisper_model, self.kokoro_model, self.kokoro_voices)):
            raise ValueError('enable voice before supplying voice paths')


@dataclass(frozen=True)
class SetupSelection:
    role: str
    model: ModelSelection | None = None
    voice: VoiceSelection = field(default_factory=VoiceSelection)
    platform: str = field(default_factory=lambda: {'Darwin': 'macos', 'Windows': 'windows'}.get(platform.system(), platform.system().lower()))
    architecture: str = field(default_factory=lambda: {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(platform.machine().lower(), platform.machine().lower()))

    def __post_init__(self):
        if self.role not in ROLES:
            raise ValueError('role must be coordinator, execution or combined')
        if self.platform not in ('windows', 'linux', 'macos') or self.architecture not in ('x86_64', 'arm64'):
            raise ValueError('unsupported setup platform or architecture')
        if self.model is not None and not isinstance(self.model, ModelSelection):
            raise ValueError('model must be a ModelSelection')
        if not isinstance(self.voice, VoiceSelection):
            raise ValueError('voice must be a VoiceSelection')
        if self.role == 'execution' and (self.model or self.voice.enabled):
            raise ValueError('execution nodes do not configure models or voice')

    @classmethod
    def from_dict(cls, data: object) -> SetupSelection:
        data = dict(fields(data, {'role', 'model', 'voice', 'platform', 'architecture'}, 'selection'))
        if data.get('model') is not None:
            data['model'] = ModelSelection.from_dict(data['model'])
        if 'voice' in data:
            data['voice'] = VoiceSelection(**fields(data['voice'], {'enabled', 'whisper_bin', 'whisper_model', 'kokoro_model', 'kokoro_voices'}, 'voice'))
        try:
            return cls(**data)
        except TypeError:
            raise ValueError('selection is missing required fields') from None


@dataclass(frozen=True)
class DiscoveryRequest:
    endpoint: str | None = None
    timeout: float = 3.0

    def __post_init__(self):
        if self.endpoint is not None:
            object.__setattr__(self, 'endpoint', endpoint_url(self.endpoint))
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)) or not math.isfinite(self.timeout) or not 0 < self.timeout <= 10:
            raise ValueError('discovery timeout must be greater than 0 and at most 10 seconds')


@dataclass(frozen=True)
class InstalledModel:
    name: str
    size_bytes: int | None = None


@dataclass(frozen=True)
class DiscoveryReport:
    status: str
    endpoint: str | None = None
    models: tuple[InstalledModel, ...] = ()
    error: str | None = None


def public(value):
    """JSON-safe contract encoding; contracts contain references, never credentials."""
    return asdict(value)
