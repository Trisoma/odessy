"""Connect an assembled application to the pinned real model and verify a small turn."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

from installer.download import DownloadError, _reject_link
from installer.manifest import _unique_keys
from installer.model import load_release as model_release, verify_model, verify_service
from installer.ollama import local_endpoint, write_config
from installer.preflight import default_locations
from installer.process import child_env
from installer.session_process import run_application


def inspect_application(app: Path) -> dict:
    if not app.is_absolute():
        raise ValueError('Application directory must be absolute.')
    _reject_link(app)
    settings = json.loads((app / 'odessy-application.json').read_text(encoding='utf-8'), object_pairs_hook=_unique_keys)
    fields = {'schema_version', 'status', 'runtime_dir', 'dependency_dir', 'python', 'runtime_versions',
              'source_sha256', 'tool_sha256'}
    if (not isinstance(settings, dict) or set(settings) != fields or type(settings['schema_version']) is not int
        or settings['schema_version'] != 1 or settings['status'] != 'ready'):
        raise ValueError('Application assembly marker is incomplete or unsupported.')
    for key in ('runtime_dir', 'dependency_dir', 'python'):
        if not isinstance(settings[key], str) or not Path(settings[key]).is_absolute():
            raise ValueError('Application component paths must be absolute.')
    if Path(settings['python']).resolve() != (Path(settings['dependency_dir']) / 'venv/Scripts/python.exe').resolve():
        raise ValueError('Application interpreter does not match its private environment.')
    files = settings['source_sha256']
    if not isinstance(files, dict) or 'src/odessy/__main__.py' not in files:
        raise ValueError('Application source snapshot is missing.')
    for name, checksum in files.items():
        if (not isinstance(name, str) or '\\' in name or ':' in name or '..' in Path(name).parts
            or Path(name).is_absolute() or not isinstance(checksum, str) or not re.fullmatch('[0-9a-f]{64}', checksum)):
            raise ValueError('Invalid assembled source fingerprint.')
        path = app / name; _reject_link(path)
        if not path.is_file() or hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest() != checksum:
            raise ValueError('Assembled source changed: ' + name)
    for path in (Path(settings['python']), Path(settings['runtime_dir']) / 'node-runtime/node.exe',
                 app / 'launch.py', app / 'tools/dist/src/server.js'):
        _reject_link(path)
        if not path.is_file():
            raise ValueError('Missing installed component: ' + str(path))
    return settings


def smoke_environment(settings: dict, endpoint: str, model: str, config: Path, *, timeout: int) -> dict:
    env = {key: value for key, value in child_env(Path(settings['runtime_dir'])).items()
           if not key.upper().startswith('ODESSY_') and key.upper() not in {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY'}}
    # No user profiles, external MCP servers, preapproved commands or cloud routing.
    env.update(ODESSY_CONFIG_FILE=str(config), ODESSY_SERVERS_FILE=str(config.parent / 'absent-servers.toml'),
               ODESSY_COMMANDS_FILE=str(config.parent / 'absent-commands.toml'), ODESSY_AUDIT_FILE='',
               ODESSY_DEPLOYMENT='single', ODESSY_MODEL_BACKEND='lfm', ODESSY_MODEL_ENDPOINT=local_endpoint(endpoint),
               ODESSY_MODEL_NAME=model, ODESSY_OLLAMA_MODEL=model, ODESSY_DEBUG_MODEL_REPAIR='1',
               # Match the bounded allowance used by normal installed sessions.
               ODESSY_MODEL_NUM_PREDICT='2048', ODESSY_MODEL_NUM_CTX='8192', ODESSY_MAX_MODEL_CALLS='3',
               ODESSY_MODEL_PROGRESS_TIMEOUT=str(min(timeout, 120)), ODESSY_MODEL_CALL_TIMEOUT=str(timeout),
               PYTHONDONTWRITEBYTECODE='1', NO_PROXY='*', no_proxy='*')
    return env


def verify_connection(app: Path, models: Path, endpoint: str, *, timeout: int = 180,
                      inspect=inspect_application, verify_files=verify_model, check_service=verify_service,
                      run=run_application, cancelled=lambda: False) -> dict:
    def check_cancelled():
        if cancelled(): raise KeyboardInterrupt()

    check_cancelled()
    if type(timeout) is not int or not 15 <= timeout <= 600:
        raise ValueError('Per-stage timeout must be an integer from 15 to 600 seconds.')
    endpoint = local_endpoint(endpoint)
    print('[CHECK] Installed application and pinned model files...', flush=True)
    settings = inspect(app)
    model = verify_files(models)
    check_cancelled()
    check_service(endpoint, model)
    report = dict(schema_version=1, app_dir=str(app), endpoint=endpoint, model=model['model'],
                  manifest_sha256=model['manifest_sha256'], model_files_verified=True, service_digest_verified=True,
                  text_response_verified=False, tool_round_trip_verified=False, inference_verified=False,
                  installation_ready=False)
    with tempfile.TemporaryDirectory(prefix='odessy-real-model-check-') as scratch:
        root = Path(scratch); workspace = root / 'workspace'; workspace.mkdir()
        config = root / 'settings/profile.env'; config.parent.mkdir()
        config.write_text('ODESSY_DEPLOYMENT=single\n', encoding='utf-8')
        nonce = uuid.uuid4().hex
        answer = 'ODESSY_OK_' + nonce
        filename = 'odessy-check-' + uuid.uuid4().hex + '.txt'
        fixture = workspace / filename; fixture.write_text('Odessy installer temporary fixture.\n', encoding='utf-8')
        original = fixture.read_bytes()
        env = smoke_environment(settings, endpoint, model['model'], config, timeout=timeout)
        command = [settings['python'], '-I', '-B', str(app / 'launch.py'), '--workspace', str(workspace), '--', '--model-backend', 'lfm']
        check_cancelled()
        print('[RUN] Real-model text response through the installed application...', flush=True)
        text = run(command + [f'Reply with exactly {answer}. Do not call tools.'], cwd=workspace, env=env,
                   label='Real-model text response', timeout=timeout)
        check_cancelled()
        # The marker must appear in a parsed final response, not an echoed input error.
        if not re.search(r'parsed final response:\s*' + re.escape(answer) + r'(?:\s|$)', text):
            raise ValueError('Real-model text response did not return the requested final response marker.')
        report['text_response_verified'] = True
        print('[RUN] Real-model filesystem.list tool round trip in an isolated workspace...', flush=True)
        request = ('Use filesystem.list with path "." to list the current directory. '
                   'Then report the file names exactly. Do not create, modify or delete files. Do not use shell commands.')
        output = run(command + [request], cwd=workspace, env=env,
                     label='Real-model directory tool round trip', timeout=timeout)
        check_cancelled()
        if '"name": "filesystem.list"' not in output or filename not in output:
            raise ValueError('Tool round trip did not show filesystem.list and the undisclosed fixture filename.')
        if not fixture.is_file() or fixture.read_bytes() != original or set(workspace.iterdir()) != {fixture}:
            raise ValueError('Isolated tool-check workspace changed unexpectedly.')
        report['tool_round_trip_verified'] = True
    # Refuse a success record if the endpoint's tag changed during the check.
    check_cancelled()
    check_service(endpoint, model)
    check_cancelled()
    report['inference_verified'] = True
    return report


def main(argv=None):
    app, models = default_locations(os.environ, Path.home())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', type=Path, default=app / 'application')
    parser.add_argument('--model-dir', type=Path, default=models)
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--timeout', type=int, default=180, help='Per application-turn deadline, 15 to 600 seconds.')
    parser.add_argument('--run', action='store_true', help='Explicitly load the real model and run two isolated application checks.')
    parser.add_argument('--config-file', type=Path, help='Optional new persistent profile to create only after successful checks.')
    parser.add_argument('--write-config', action='store_true')
    args = parser.parse_args(argv)
    try:
        if not 15 <= args.timeout <= 600:
            raise ValueError('Per-stage timeout must be from 15 to 600 seconds.')
        endpoint = local_endpoint(args.endpoint)
        inspect_application(args.app_dir)
        _, resources, _ = model_release()
        if args.write_config and (args.config_file is None or not args.run):
            raise ValueError('--write-config requires --run and an explicit new --config-file.')
        if args.config_file is not None:
            if not args.config_file.is_absolute():
                raise ValueError('Configuration file must be absolute.')
            if args.write_config and (args.config_file.exists() or args.config_file.is_symlink()):
                raise ValueError('Existing configuration is preserved; select a new --config-file.')
        if not args.run:
            print('Application:', args.app_dir, '\nPinned model:', resources['model'], '\nEndpoint:', endpoint)
            print('PREVIEW ONLY. --run loads the real model and runs two temporary-workspace checks.')
            print('No inference, service requests or configuration writes performed.')
            return 0
        report = verify_connection(args.app_dir, args.model_dir, endpoint, timeout=args.timeout)
        if args.write_config:
            write_config(args.config_file, {'service_status': 'available', 'endpoint': endpoint, 'model': report['model']})
            print('New local-model profile:', args.config_file)
        print(json.dumps(report, indent=2))
        print('Real text response and one directory-tool round trip passed. This is not a capability/performance benchmark.')
        print('No user workspace or existing configuration modified; graphical installation is still pending.')
        return 0
    except KeyboardInterrupt:
        print('Cancelled; owned application/tool processes and temporary workspace cleaned.')
        return 130
    except (OSError, ValueError, DownloadError) as error:
        print('Application/model connection check failed:', error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
