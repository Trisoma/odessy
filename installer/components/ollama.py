"""Inspect an existing local Ollama service and optionally create a new profile."""
from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import math
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from installer.core.preflight import REPO_ROOT, repository_defaults

MAX_RESPONSE_BYTES = 1024 * 1024
MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\Z")


def local_endpoint(value: str) -> str:
    """This milestone supports plain HTTP on a literal loopback address only."""
    if any(ord(c) <= 32 for c in value) or "?" in value or "#" in value:
        raise ValueError('Endpoint must be a local HTTP base URL without query or fragment.')
    url = urlsplit(value)
    if url.scheme != 'http' or url.username is not None or url.password is not None or url.path not in ('', '/'):
        raise ValueError('Use a local HTTP base URL, without credentials or an API path.')
    host = url.hostname
    if host == 'localhost':
        host = '127.0.0.1'
    try:
        address = ipaddress.ip_address(host or '')
    except ValueError as error:
        raise ValueError('This milestone accepts only localhost or a literal loopback IP.') from error
    if not address.is_loopback:
        raise ValueError('This milestone accepts only a local loopback service.')
    port = url.port if url.port is not None else 11434
    if not 1 <= port <= 65535:
        raise ValueError('Endpoint port must be between 1 and 65535.')
    authority = f'[{address}]' if address.version == 6 else str(address)
    return 'http://' + authority + ':' + str(port)


def validate_model(model: str) -> str:
    # These values are later written to an env file: reject expansion and lines.
    if not MODEL_PATTERN.fullmatch(model):
        raise ValueError('Model must be a plain Ollama model name/tag (at most 256 characters).')
    return model


def get_json(endpoint: str, path: str, timeout: float) -> dict:
    """No proxy, redirects, authentication, subprocesses or model inference."""
    url = urlsplit(local_endpoint(endpoint))
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
    deadline = time.monotonic() + timeout
    try:
        connection.request('GET', path, headers={'Accept': 'application/json', 'Connection': 'close'})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f'{path} returned HTTP {response.status}; redirects are not followed.')
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdecimal() or int(length) > MAX_RESPONSE_BYTES):
            raise ValueError(f'{path} response exceeds the size limit or has invalid length.')
        body = bytearray()
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError(f'{path} response exceeded its time budget.')
            chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError(f'{path} response exceeds the size limit.')
        if length is not None and len(body) != int(length):
            raise ValueError(f'{path} response is truncated.')
        data = json.loads(body.decode('utf-8'))
        if not isinstance(data, dict):
            raise ValueError(f'{path} must return a JSON object.')
        return data
    finally:
        connection.close()


def inspect_service(endpoint: str, model: str, *, timeout: float = 3.0, fetch=get_json) -> dict:
    endpoint = local_endpoint(endpoint)
    validate_model(model)
    if not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError('Timeout must be greater than zero and at most 30 seconds.')
    report = dict(schema_version=1, endpoint=endpoint, model=model, version=None,
                  service_status='unavailable', model_present=False, models=[],
                  inference_verified=False, installation_ready=False,
                  next_step='Check whether Ollama is installed and running; then retry.')
    try:
        version = fetch(endpoint, '/api/version', timeout)
        report['service_status'] = 'invalid_response'
        value = version.get('version')
        if not isinstance(value, str) or len(value) > 100 or not VERSION_PATTERN.fullmatch(value):
            raise ValueError('Ollama version response is missing a valid version string.')
        report['version'] = value
        tags = fetch(endpoint, '/api/tags', timeout)
        entries = tags.get('models')
        if not isinstance(entries, list):
            raise ValueError('Ollama tags response must contain a models array.')
        names = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('name'), str):
                raise ValueError('Ollama tags response contains an invalid model entry.')
            names.add(validate_model(entry['name']))
        report['models'] = sorted(names)
        # A missing tag means :latest; a registry port is not a model tag.
        wanted = model if ':' in model.rsplit('/', 1)[-1] else model + ':latest'
        report['model_present'] = model in names or wanted in names
        report['service_status'] = 'available'
        report['next_step'] = ('Model is listed; real inference/tool-calling validation is still pending.'
                               if report['model_present'] else
                               'Service is available; selected model download is a later module.')
    except (OSError, ValueError, http.client.HTTPException) as error:
        report['service_status'] = ('invalid_response' if isinstance(error, (ValueError, http.client.HTTPException))
                                    else 'unavailable')
        report['error'] = str(error)
        if report['service_status'] == 'invalid_response':
            report['next_step'] = 'Check the local service API response before reusing it.'
    return report


def proposed_config(report: dict) -> dict[str, str]:
    return {'ODESSY_DEPLOYMENT': 'single', 'ODESSY_MODEL_BACKEND': 'lfm',
            'ODESSY_MODEL_ENDPOINT': local_endpoint(report['endpoint']),
            'ODESSY_MODEL_NAME': validate_model(report['model']),
            'ODESSY_OLLAMA_MODEL': validate_model(report['model'])}


def write_config(path: Path, report: dict) -> None:
    """Create only; never merge into or overwrite an existing user configuration."""
    if report['service_status'] != 'available':
        raise ValueError('Configuration cannot be created until both service probes pass.')
    if not path.is_absolute():
        raise ValueError('Configuration file must have an absolute path.')
    content = '# Local text-mode profile created by the Odessy installer.\n'
    content += '# Model listing is checked; real inference is not verified.\n'
    content += ''.join(f'{key}={value}\n' for key, value in proposed_config(report).items())
    if path.exists() or path.is_symlink():
        raise FileExistsError('Existing configuration is preserved; choose a new --config-file.')
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also handles a file appearing after the existence check.
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(content)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', help='Local HTTP base URL; defaults to the repository configuration.')
    parser.add_argument('--model', help='Model name/tag; defaults to the repository configuration.')
    parser.add_argument('--timeout', type=float, default=3.0, help='Per-probe time budget, 0 < seconds <= 30.')
    parser.add_argument('--config-file', type=Path, help='New profile path; otherwise use ODESSY_CONFIG_FILE or the user default.')
    parser.add_argument('--write-config', action='store_true', help='Explicitly create a new configuration; existing files are preserved.')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        defaults = repository_defaults(REPO_ROOT / 'config' / 'odessy.env')
        report = inspect_service(args.endpoint or defaults['ODESSY_MODEL_ENDPOINT'],
                                 args.model or defaults['ODESSY_MODEL_NAME'], timeout=args.timeout)
        target = args.config_file or Path(os.environ.get('ODESSY_CONFIG_FILE') or
                                         Path.home() / '.config' / 'odessy' / 'odessy.env')
        if not target.is_absolute():
            raise ValueError('Configuration file must have an absolute path.')
        report['configuration'] = {'path': str(target), 'exists': target.exists(), 'written': False,
                                   'proposed': proposed_config(report)}
        report['environment_overrides'] = sorted(key for key in
            (*report['configuration']['proposed'], 'ODESSY_CONFIG_FILE', 'ODESSY_TOOL_SERVER_URL')
            if os.environ.get(key))
        if args.write_config:
            try:
                write_config(target, report)
                report['configuration']['written'] = True
                report['configuration']['exists'] = True
            except (OSError, ValueError) as error:
                report['configuration']['error'] = str(error)
        if args.json:
            print(json.dumps(report, ensure_ascii=True, indent=2))
        else:
            print('Ollama service:', report['service_status'], '(' + report['endpoint'] + ')')
            print('Version:', report['version'] or 'not detected')
            print('Selected model:', report['model'], '- listed' if report['model_present'] else '- not confirmed present')
            if report.get('error'):
                print('Probe error:', report['error'])
            print('Configuration:', target, '- created' if report['configuration']['written'] else '- unchanged')
            if report['configuration'].get('error'):
                print('Configuration error:', report['configuration']['error'])
            if report['environment_overrides']:
                print('Existing environment settings may override this profile:', ', '.join(report['environment_overrides']))
            print('Next:', report['next_step'])
            print('No software or models downloaded, no services started, no inference performed.')
            print('Real inference and complete installation are not verified by this stage.')
        if report['configuration'].get('error') or report['service_status'] != 'available':
            return 1
        return 0 if report['model_present'] else 2
    except (OSError, ValueError) as error:
        if args.json:
            print(json.dumps({'error': str(error), 'installation_ready': False}))
        else:
            print('Ollama inspection failed:', error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
