"""Observational readiness checks; no inference, tool calls or service starts."""
from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

from .contracts import DiscoveryReport, SetupSelection

KEY_ENV = {'claude': 'ANTHROPIC_API_KEY', 'openai': 'OPENAI_API_KEY', 'deepseek': 'DEEPSEEK_API_KEY',
           'qwen': 'DASHSCOPE_API_KEY', 'kimi': 'MOONSHOT_API_KEY', 'custom': 'ODESSY_CLOUD_API_KEY'}


def credential_value(model, environ=None):
    """Resolve an explicit reference exactly; never persist or return it to a UI."""
    from odessy.credentials import Scope, resolve
    return resolve(Scope(model.provider, model.endpoint, model.credential_env), environ=environ) or ''


def verify(selection: SetupSelection, discovery: DiscoveryReport | None = None) -> dict:
    local = SetupSelection(role=selection.role)
    if (selection.platform, selection.architecture) != (local.platform, local.architecture):
        raise ValueError('selection platform/architecture belongs to another machine; verify on its owning host')
    model = {'status': 'not_configured'}
    if selection.model:
        chosen = selection.model
        model = {'status': 'not_checked', 'reason': 'No inference was requested; configuration is not model compatibility verification.'}
        if chosen.provider in KEY_ENV:
            key = chosen.credential_env or KEY_ENV[chosen.provider]
            available = bool(credential_value(chosen))
            model = {'status': 'configured' if available else 'needs_credentials', 'credential_env': key,
                     'reason': 'No cloud request sent.'}
        elif discovery and discovery.status != 'not_requested':
            installed = (discovery.endpoint == chosen.endpoint and discovery.status == 'ok'
                         and chosen.name in {item.name for item in discovery.models})
            model = {'status': 'available_unverified' if installed else 'unavailable',
                     'reason': 'Inventory listing does not prove inference/tool compatibility.'}
    components = {}
    if selection.role != 'coordinator':
        components['node'] = 'found_unverified' if shutil.which('node') else 'missing'
    if selection.role != 'combined':
        components['odessy-link'] = 'found_unverified' if shutil.which('odessy-link') else 'missing'
    voice = {'status': 'disabled'}
    if selection.voice.enabled:
        missing = []
        names = (['whisper_bin', 'whisper_model'] if selection.voice.stt_provider == 'whisper.cpp' else []) + (['kokoro_model', 'kokoro_voices'] if selection.voice.tts_provider == 'kokoro' else [])
        names += [name for name in ('stt_executable', 'stt_model', 'tts_executable', 'tts_model') if getattr(selection.voice, name)]
        for name in names:
            value = getattr(selection.voice, name)
            if name == 'whisper_bin' or name.endswith('_executable'):
                resolved = shutil.which(value) if value else None
                exists = bool(resolved and Path(resolved).is_file())
            else:
                exists = bool(value and Path(value).expanduser().is_file())
            if not exists:
                missing.append(name)
        for module in (('kokoro_onnx', 'onnxruntime') if selection.voice.tts_provider == 'kokoro' else ()):
            if importlib.util.find_spec(module) is None:
                missing.append(module)
        voice = {'status': 'missing' if missing else 'assets_present_unverified', 'missing': missing,
                 'reason': 'No microphone capture, speech synthesis or transcription performed.'}
    return {'model': model, 'voice': voice, 'components': components,
            'installation': 'not_performed', 'pairing': 'not_performed', 'ready': False}
