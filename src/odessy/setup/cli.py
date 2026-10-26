"""Headless setup frontend; safe plan by default, explicit create-only exports."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .configuration import consent_id, destination, export_environment, export_selection
from .contracts import DiscoveryRequest, ModelSelection, ROLES, SetupSelection, VoiceSelection, public
from .discovery import discover
from .planning import build_plan
from .verification import verify

MAX_CONFIG_BYTES = 65536


def _load(path: str) -> SetupSelection:
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise ValueError('setup configuration exceeds 64 KiB')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate setup configuration field')
            result[key] = value
        return result
    data = json.loads(raw, object_pairs_hook=unique)
    if isinstance(data, dict) and 'selection' in data:
        if set(data) != {'schema_version', 'selection'} or type(data['schema_version']) is not int or data['schema_version'] != 1:
            raise ValueError('unsupported setup configuration schema')
        data = data['selection']
    return SetupSelection.from_dict(data)


def main(argv: list[str] | None = None, *, input_stream=None, output_stream=None, error_stream=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ['manage']:
        from .manage import main as manage_main
        return manage_main(arguments[1:])
    parser = argparse.ArgumentParser(prog='odessy setup', description='Configure existing components without installing services or downloading models.')
    parser.add_argument('--role', choices=ROLES)
    parser.add_argument('--config', help='Validated selection JSON or a previous setup export.')
    parser.add_argument('--provider', help='ollama, lfm, or a supported cloud provider; never downloads a model.')
    parser.add_argument('--model', help='Explicit existing model name; no default LFM.')
    parser.add_argument('--endpoint', help='Explicit model endpoint (discovery only when --discover is supplied).')
    parser.add_argument('--credential-env', default='', help='Credential environment-variable reference; never the key itself.')
    parser.add_argument('--voice', action='store_true', help='Opt in to configured speech providers (Whisper.cpp/Kokoro by default).')
    parser.add_argument('--stt-provider', default='whisper.cpp')
    parser.add_argument('--tts-provider', default='kokoro')
    for name in ('whisper-bin', 'whisper-model', 'kokoro-model', 'kokoro-voices', 'stt-executable', 'stt-model', 'tts-executable', 'tts-model'):
        parser.add_argument('--' + name, default='')
    parser.add_argument('--discover', action='store_true', help='Read only the selected Ollama endpoint model inventory.')
    parser.add_argument('--timeout', type=float, default=3.0)
    parser.add_argument('--verify', action='store_true', help='Check existing components without inference or service starts.')
    parser.add_argument('--non-interactive', action='store_true')
    parser.add_argument('--plan', action='store_true', help='Explicit read-only review (the default).')
    targets = parser.add_mutually_exclusive_group()
    targets.add_argument('--output', help='Save a new selection JSON after exact plan confirmation.')
    targets.add_argument('--write-env', help='Export a new runtime env file after exact plan confirmation; never overwrite.')
    parser.add_argument('--confirm-plan', help='Exact plan_id from review with the same selection and destination.')
    args = parser.parse_args(argv)
    stdin, stdout, stderr = input_stream or sys.stdin, output_stream or sys.stdout, error_stream or sys.stderr
    try:
        if args.config:
            if any((args.role, args.provider, args.model, args.endpoint, args.voice, args.credential_env,
                    args.whisper_bin, args.whisper_model, args.kokoro_model, args.kokoro_voices,
                    args.stt_provider != 'whisper.cpp', args.tts_provider != 'kokoro',
                    args.stt_executable, args.stt_model, args.tts_executable, args.tts_model)):
                raise ValueError('--config cannot be combined with selection overrides')
            selection = _load(args.config)
        else:
            role = args.role
            if not role:
                if args.non_interactive or not stdin.isatty():
                    raise ValueError('explicit --role is required without an interactive terminal')
                print('Role (coordinator / execution / combined): ', end='', file=stderr, flush=True)
                role = stdin.readline().strip()
            if args.provider and not args.model or args.model and not args.provider:
                raise ValueError('--provider and --model must be supplied together')
            if args.credential_env and not args.model:
                raise ValueError('--credential-env requires a model')
            voice = VoiceSelection(args.voice, args.whisper_bin, args.whisper_model, args.kokoro_model, args.kokoro_voices,
                args.stt_provider, args.tts_provider, args.stt_executable, args.stt_model, args.tts_executable, args.tts_model)
            model = ModelSelection(args.provider, args.model, args.endpoint or '', args.credential_env) if args.model else None
            selection = SetupSelection(role=role, model=model, voice=voice)
        endpoint = selection.model.endpoint if selection.model and selection.model.provider in ('ollama', 'lfm') else args.endpoint
        if args.discover and selection.model and selection.model.provider not in ('ollama', 'lfm'):
            raise ValueError('--discover is only supported for an Ollama endpoint, not cloud inference')
        if args.discover and selection.role == 'execution':
            raise ValueError('execution nodes do not discover models')
        request = DiscoveryRequest(endpoint=(endpoint or 'http://127.0.0.1:11434') if args.discover else None, timeout=args.timeout)
        report = discover(request)
        plan = build_plan(selection, report)
        output = destination(args.output) if args.output else None
        env_output = destination(args.write_env) if args.write_env else None
        result = public(plan)
        result['component_plan_id'] = result['plan_id']
        result['plan_id'] = consent_id(plan, output, env_output)
        result['discovery'] = public(report)
        result['writes'] = [str(path) for path in (output, env_output) if path]
        if args.confirm_plan:
            if args.plan or not (output or env_output):
                raise ValueError('confirmation requires one export destination and cannot accompany --plan')
            if args.confirm_plan != result['plan_id']:
                raise ValueError('plan changed or confirmation does not match; review again')
            if output:
                export_selection(plan, output)
            else:
                export_environment(plan, env_output)
            result['saved'] = True
        if args.verify:
            checks = verify(selection, report)
            result = {'plan': result, 'verification': checks}
        print(json.dumps(result, indent=2), file=stdout)
        return 0
    except (ValueError, TypeError, OSError) as error:
        # Avoid echoing user JSON, HTTP bodies, credential-bearing URLs or paths.
        message = str(error) if isinstance(error, ValueError) else 'configuration could not be read or saved; check fields, destination and permissions'
        print(json.dumps({'error': message}), file=stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
