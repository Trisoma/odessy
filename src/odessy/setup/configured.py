"""Start a source/preview coordinator from an exactly reviewed saved export."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from .configuration import destination, environment_text
from .contracts import SetupSelection, public
from .planning import build_plan
from .maintenance import default_workspace


def saved_spec(selection, output, format_):
    plan = build_plan(selection)
    if format_ not in ('selection', 'env'):
        raise ValueError('unsupported export format')
    expected = (json.dumps({'schema_version': plan.schema_version, 'selection': public(selection)}, indent=2) + '\n'
                if format_ == 'selection' else environment_text(plan)).encode()
    path = destination(output)
    if not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('Save the reviewed configuration file before connecting.')
    raw = path.read_bytes()
    if raw != expected:
        raise ValueError('Saved configuration does not match the reviewed selection; review again.')
    return {'configured': selection.model is not None and selection.role != 'execution', 'python': sys.executable, 'pythonpath': str(Path(__file__).resolve().parents[2]), 'config': str(path), 'format': format_,
            'selection': public(selection), 'sha256': hashlib.sha256(raw).hexdigest(),
            'workspace': str(default_workspace())}


def desktop_state_file():
    return Path.home() / '.config' / 'odessy' / 'desktop.json'


def remember(spec):
    """Remember only an exact verified export; never persist credentials/tokens."""
    import tempfile
    path = desktop_state_file()
    if path.is_symlink():
        raise ValueError('Desktop state must not be a symbolic link.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        if path.stat().st_size > 65536 or json.loads(path.read_text()).get('owner') != 'odessy-desktop-v1':
            raise ValueError('Existing desktop state is not owned by Odessy.')
    data = {key: spec[key] for key in ('config', 'format', 'selection', 'sha256', 'workspace')}
    data.update(owner='odessy-desktop-v1', schema_version=1)
    fd, name = tempfile.mkstemp(prefix='.desktop-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def resume():
    path = desktop_state_file()
    if not path.exists():
        # A source preview may predate the remembered-configuration pointer.
        for previous in (Path.home() / 'odessy-setup.json', Path.home() / 'odessy-runtime.env'):
            if not previous.is_file():
                continue
            if previous.is_symlink() or previous.stat().st_size > 65536:
                raise ValueError('Invalid saved desktop configuration.')
            if previous.suffix == '.json':
                data = json.loads(previous.read_text(encoding='utf-8'))
                selection = SetupSelection.from_dict(data['selection'])
                return saved_spec(selection, previous, 'selection')
            # Only parse the exact create-only environment format written by setup.
            values = {}
            for line in previous.read_text(encoding='utf-8').splitlines():
                if not line or line.startswith('#'): continue
                key, sep, value = line.partition('=')
                if not sep or not value.startswith('"') or not value.endswith('"'):
                    raise ValueError('Review this environment file in setup before connecting.')
                values[key] = value[1:-1]
            provider = values.get('ODESSY_MODEL_BACKEND')
            if not provider: raise ValueError('Saved configuration has no model provider.')
            local = provider in ('ollama', 'lfm')
            voice_keys = ('whisper_bin','whisper_model','kokoro_model','kokoro_voices','stt_provider','tts_provider','stt_executable','stt_model','tts_executable','tts_model','audio_backend','audio_input_device','audio_output_device','wake_model')
            voice = {name: values['ODESSY_'+name.upper()] for name in voice_keys if 'ODESSY_'+name.upper() in values}
            selection = SetupSelection.from_dict({'role': 'combined' if values.get('ODESSY_DEPLOYMENT') == 'single' else 'coordinator',
                'model': {'provider': provider, 'name': values.get('ODESSY_MODEL_NAME' if local else 'ODESSY_CLOUD_MODEL', ''),
                          'endpoint': values.get('ODESSY_MODEL_ENDPOINT' if local else 'ODESSY_CLOUD_BASE_URL', '')},
                'voice': {'enabled': bool(voice), **voice}})
            return saved_spec(selection, previous, 'env')
        return {'configured': False}
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError('Invalid desktop state file.')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('owner') != 'odessy-desktop-v1' or data.get('schema_version') != 1:
        raise ValueError('Invalid desktop configuration state.')
    spec = saved_spec(SetupSelection.from_dict(data['selection']), data['config'], data['format'])
    if spec['sha256'] != data['sha256']:
        raise ValueError('Saved configuration changed; review it before reconnecting.')
    if data.get('workspace'): spec['workspace'] = data['workspace']
    return spec


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--format', choices=('selection', 'env'), required=True)
    parser.add_argument('--selection-json', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--expected-fingerprint')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--exit-on-stdin-close', action='store_true')
    args = parser.parse_args(argv)
    selection = SetupSelection.from_dict(json.loads(args.selection_json))
    spec = saved_spec(selection, args.config, args.format)
    if spec['sha256'] != args.sha256:
        raise ValueError('Configuration changed after launch review.')
    if not 1 <= args.port <= 65535:
        raise ValueError('invalid loopback port')
    token = os.environ.get('ODESSY_API_TOKEN', '').strip()
    if not token:
        raise ValueError('An authenticated desktop launch token is required.')
    workspace = Path(args.workspace).expanduser()
    from odessy.application.capabilities import configuration_descriptor, configuration_owner
    if args.expected_fingerprint and configuration_descriptor(selection, workspace, owner=configuration_owner())['fingerprint'] != args.expected_fingerprint:
        raise ValueError('Saved configuration changed after restart review. Review it again.')
    workspace.mkdir(parents=True, exist_ok=True)
    from .service_entry import run
    run(selection, workspace=workspace, port=args.port, token=token, watch_stdin=args.exit_on_stdin_close,
        configuration_owner=configuration_owner())

if __name__ == '__main__':
    main()
