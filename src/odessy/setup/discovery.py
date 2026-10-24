"""Explicit bounded Ollama inventory reads; never inference or model pulls."""
from __future__ import annotations

import json
import http.client
import time
import urllib.error
import urllib.request

from .contracts import DiscoveryReport, DiscoveryRequest, InstalledModel, text

MAX_BYTES = 1024 * 1024
MAX_MODELS = 256


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def discover(request: DiscoveryRequest) -> DiscoveryReport:
    if request.endpoint is None:
        return DiscoveryReport('not_requested')
    try:
        opener = urllib.request.build_opener(NoRedirect())
        started = time.monotonic()
        with opener.open(request.endpoint + '/api/tags', timeout=request.timeout) as response:
            chunks = []; size = 0
            while True:
                if time.monotonic() - started > request.timeout:
                    raise TimeoutError
                chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                size += len(chunk)
                if size > MAX_BYTES:
                    return DiscoveryReport('invalid_response', request.endpoint, error='model inventory exceeds 1 MiB')
                if not chunk:
                    break
                chunks.append(chunk)
        data = json.loads(b''.join(chunks))
        entries = data.get('models') if isinstance(data, dict) else None
        if not isinstance(entries, list) or len(entries) > MAX_MODELS:
            raise ValueError
        models = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError
            name = text(entry.get('name'), 'installed model name')
            size = entry.get('size')
            if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
                raise ValueError
            models[name] = InstalledModel(name, size)
        return DiscoveryReport('ok' if models else 'empty', request.endpoint, tuple(models[name] for name in sorted(models)))
    except (ValueError, UnicodeError):
        return DiscoveryReport('invalid_response', request.endpoint, error='invalid Ollama model inventory')
    except (OSError, urllib.error.URLError, TimeoutError, http.client.HTTPException):
        return DiscoveryReport('unreachable', request.endpoint, error='model endpoint unavailable or timed out; installed models are unknown')
