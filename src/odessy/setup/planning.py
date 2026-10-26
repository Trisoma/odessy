"""Pure, deterministic setup review; no downloads or permission changes."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .contracts import DiscoveryReport, SCHEMA_VERSION, SetupSelection, public


@dataclass(frozen=True)
class SetupPlan:
    schema_version: int
    plan_id: str
    selection: SetupSelection
    components: tuple[str, ...]
    downloads: tuple[str, ...]
    environment: dict[str, str]
    readiness: str
    warnings: tuple[str, ...]


def build_plan(selection: SetupSelection, discovery: DiscoveryReport | None = None) -> SetupPlan:
    components = ['python', 'odessy']
    warnings = ['Configuration planning only: runtimes, services, pairing and workspace grants are not installed or changed.']
    env = {}
    if selection.role != 'coordinator':
        components.extend(['node', 'odessy-tools'])
    if selection.role != 'combined':
        components.append('odessy-link')
    model = selection.model
    if model:
        env['ODESSY_MODEL_BACKEND'] = model.provider
        env['ODESSY_DEPLOYMENT'] = 'single' if selection.role == 'combined' else 'split'
        if model.provider in ('ollama', 'lfm'):
            components.append('ollama')
            env.update(ODESSY_MODEL_NAME=model.name, ODESSY_OLLAMA_MODEL=model.name,
                       ODESSY_OLLAMA_URL=model.endpoint, ODESSY_MODEL_ENDPOINT=model.endpoint)
            if discovery and discovery.status in ('ok', 'empty'):
                if discovery.endpoint != model.endpoint:
                    raise ValueError('discovery belongs to a different model endpoint')
                if model.name not in {item.name for item in discovery.models}:
                    raise ValueError('selected model is not installed at the discovered endpoint; no download will be attempted')
        else:
            env['ODESSY_CLOUD_MODEL'] = model.name
            if model.endpoint:
                env['ODESSY_CLOUD_BASE_URL'] = model.endpoint
            warnings.append('Cloud credentials must be supplied through the provider environment; no credential is saved.')
        warnings.append('Selected model compatibility and tool capability have not been established by listing/configuration.')
    if selection.role == 'coordinator':
        warnings.append('A coordinator uses split deployment; configure its paired execution node separately before tool work.')
    voice = selection.voice
    if voice.enabled:
        if voice.stt_provider == 'whisper.cpp':
            components.append('whisper.cpp')
        if voice.tts_provider == 'kokoro':
            components.extend(['kokoro', 'onnxruntime'])
        for name in ('stt_provider', 'tts_provider', 'stt_executable', 'stt_model', 'tts_executable', 'tts_model'):
            if getattr(voice, name):
                env['ODESSY_' + name.upper()] = getattr(voice, name)
        for name, key in [('whisper_bin', 'ODESSY_WHISPER_BIN'), ('whisper_model', 'ODESSY_WHISPER_MODEL'),
                          ('kokoro_model', 'ODESSY_KOKORO_MODEL'), ('kokoro_voices', 'ODESSY_KOKORO_VOICES')]:
            if getattr(voice, name):
                env[key] = getattr(voice, name)
        warnings.append('Voice is optional; selected assets, executable and dependencies need verification. Wake assets are configured separately.')
    readiness = 'configured_unverified' if model else 'needs_configuration'
    payload = dict(schema_version=SCHEMA_VERSION, selection=public(selection), components=components,
                   downloads=[], environment=env, readiness=readiness, warnings=warnings)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return SetupPlan(SCHEMA_VERSION, digest, selection, tuple(components), (), env, readiness, tuple(warnings))
