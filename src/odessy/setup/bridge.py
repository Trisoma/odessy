"""One bounded request per owned local stdio helper; no remote listener."""
from __future__ import annotations

import json
import re
import sys
import subprocess

from .configuration import consent_id, destination, environment_text, export_environment, export_selection
from .contracts import DiscoveryRequest, SetupSelection, fields, public
from .discovery import discover
from .planning import build_plan
from .verification import verify

MAX_REQUEST = 65536


def execute(request: dict) -> dict:
    fields(request, {'id', 'operation', 'selection', 'output', 'format', 'confirm_plan', 'endpoint', 'parameters', 'cancel_file'}, 'request')
    operation = request.get('operation')
    if operation == 'installer':
        from installer.flutter_bridge import execute as installer_execute
        cancel_file=request.get('cancel_file')
        if cancel_file:
            from pathlib import Path
            path=Path(cancel_file)
            if not path.is_absolute() or path.name!='cancel' or not path.parent.name.startswith('odessy-setup-'):raise ValueError('invalid cancellation marker')
            return installer_execute(request.get('parameters'),cancelled=path.exists)
        return installer_execute(request.get('parameters'))
    if operation in ('manage','pair'):
        from .native_bridge import manage,pair
        if operation=='manage' and request.get('cancel_file'):
            from pathlib import Path
            path=Path(request['cancel_file'])
            if not path.is_absolute() or path.name!='cancel' or not path.parent.name.startswith('odessy-setup-'):raise ValueError('invalid cancellation marker')
            return manage(request.get('parameters'),cancelled=path.exists)
        return (manage if operation=='manage' else pair)(request.get('parameters'))
    if operation not in ('review', 'discover', 'verify', 'save', 'recover'):
        raise ValueError('unsupported setup operation')
    selection = SetupSelection.from_dict(request.get('selection'))
    model = selection.model
    if operation == 'discover':
        if selection.role == 'execution' or model and model.provider not in ('ollama', 'lfm'):
            raise ValueError('model discovery requires an Ollama coordinator')
        endpoint = request.get('endpoint') or (model.endpoint if model else 'http://127.0.0.1:11434')
        return {'discovery': public(discover(DiscoveryRequest(endpoint=endpoint)))}
    plan = build_plan(selection)
    output = destination(request['output']) if request.get('output') else None
    format_ = request.get('format', 'selection')
    if format_ not in ('selection', 'env'):
        raise ValueError('unsupported export format')
    result = public(plan)
    result['plan_id'] = consent_id(plan, output if format_ == 'selection' else None, output if format_ == 'env' else None)
    result['writes'] = [str(output)] if output else []
    result['format'] = format_
    if operation == 'verify':
        # Observational only. Explicit inventory discovery is a separate operation.
        result['verification'] = verify(selection)
    elif operation == 'save':
        if output is None or request.get('confirm_plan') != result['plan_id']:
            raise ValueError('review this selection and destination before confirming save')
        (export_selection if format_ == 'selection' else export_environment)(plan, output)
        result['saved'] = True
    elif operation == 'recover':
        if output is None:
            raise ValueError('recovery requires the reviewed export destination')
        if not output.exists():
            result['recovery'] = 'missing'
        else:
            expected = (json.dumps({'schema_version': plan.schema_version, 'selection': public(selection)}, indent=2)+'\n'
                        if format_ == 'selection' else environment_text(plan))
            with output.open('rb') as stream:
                actual = stream.read(MAX_REQUEST+1)
            result['recovery'] = 'matches' if actual == expected.encode('utf-8') else 'conflict'
    return result


def run(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    identifier = None
    try:
        raw = stdin.readline(MAX_REQUEST+1)
        if len(raw) > MAX_REQUEST or not raw.endswith(b'\n'):
            raise ValueError('setup request must be one JSON line of at most 64 KiB')
        def unique(pairs):
            values = {}
            for key, value in pairs:
                if key in values:
                    raise ValueError('duplicate request field')
                values[key] = value
            return values
        request = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(request, dict) or not isinstance(request.get('id'), str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', request['id']):
            raise ValueError('valid controller request id required')
        identifier = request['id']
        response = {'id': identifier, 'status': 'ok', 'result': execute(request)}
    except (ValueError, TypeError, OSError, RecursionError, RuntimeError, KeyError, subprocess.SubprocessError, ImportError, KeyboardInterrupt) as error:
        # A bounded, sanitized reason lets the local UI explain a failure without
        # returning raw installer stdout, environment values or secrets.
        reason = re.sub(r'\s+', ' ', str(error)).strip()[:300]
        response = {'id': identifier, 'status': 'error',
                    'error': 'Setup failed. Check selection, paths, existing files and review confirmation. No existing configuration was replaced.',
                    'reason': reason}
    stdout.write(json.dumps(response).encode('utf-8')+b'\n')
    stdout.flush()
    return 0 if response['status'] == 'ok' else 2


if __name__ == '__main__':
    raise SystemExit(run())
