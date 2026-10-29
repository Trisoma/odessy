"""Explicit, bounded model inventory. Suggestions do not make network requests."""
from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request

from .contracts import DiscoveryRequest, endpoint_url, fields, public, text
from .discovery import MAX_BYTES, MAX_MODELS, NoRedirect, discover


def catalog(parameters):
    options = fields(parameters, {'provider', 'endpoint', 'credential_env', 'refresh'}, 'model catalog')
    provider = options.get('provider')
    from odessy.models.cloud_model import CLOUD_PROVIDERS
    if provider not in (*CLOUD_PROVIDERS, 'ollama', 'lfm'):
        raise ValueError('unsupported model provider')
    refresh = options.get('refresh', False)
    if not isinstance(refresh, bool):
        raise ValueError('refresh must be a boolean')
    preset = CLOUD_PROVIDERS.get(provider)
    default = preset.default_model if preset else ''
    suggestions = [{'name': default, 'label': f'Provider default ({default})'}] if default else []
    result = {'status': 'suggestions', 'models': suggestions}
    if not refresh:
        return result
    address = options.get('endpoint') or (preset.base_url if preset else 'http://127.0.0.1:11434')
    if not address:
        return dict(result, status='endpoint_missing', error='Enter the provider endpoint first.')
    address = endpoint_url(address)
    if not preset:
        return public(discover(DiscoveryRequest(address)))
    reference = options.get('credential_env', '')
    if not isinstance(reference, str) or reference and not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', reference):
        raise ValueError('credential_env must name an environment variable')
    from odessy.credentials import Scope, resolve
    key = resolve(Scope(provider, address, reference))
    if not key and provider != 'custom':
        return dict(result, status='credentials_missing', error='Configure the provider API key in Settings, then fetch again.')
    headers = {'Accept': 'application/json'}
    if key:
        if provider == 'claude':
            headers.update({'x-api-key': key, 'anthropic-version': '2023-06-01'})
        else:
            headers['Authorization'] = 'Bearer ' + key
    suffix = '/v1/models' if provider == 'claude' and not address.endswith('/v1') else '/models'
    try:
        request = urllib.request.Request(address + suffix, headers=headers)
        opener = urllib.request.build_opener(NoRedirect())
        started = time.monotonic()
        chunks, size = [], 0
        with opener.open(request, timeout=3) as response:
            while True:
                if time.monotonic() - started > 3:
                    raise TimeoutError
                chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError
                if not chunk:
                    break
                chunks.append(chunk)
        payload = json.loads(b''.join(chunks))
        entries = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(entries, list) or len(entries) > MAX_MODELS:
            raise ValueError
        models = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError
            name = text(entry.get('id'), 'model identifier')
            display = text(entry.get('name') or entry.get('display_name') or name, 'model display name')
            models[name] = {'name': name, 'label': f'{display} ({name})' if display != name else name}
        return {'status': 'ok' if models else 'empty', 'models': [models[name] for name in sorted(models)]}
    except urllib.error.HTTPError as error:
        message = 'The provider rejected authentication. Check the API key.' if error.code in (401, 403) else 'This endpoint did not provide a model list. Use the provider default or enter a custom ID.'
        return dict(result, status='unavailable', error=message)
    except (OSError, urllib.error.URLError, TimeoutError, http.client.HTTPException, ValueError, UnicodeError):
        return dict(result, status='unavailable', error='Could not read the model list. Use the provider default or enter a custom ID.')
