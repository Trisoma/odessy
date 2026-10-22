"""Run a completed local installation; own only processes started by this session."""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from installer.components import connection

from installer.components import model

from installer.components import ollama_runtime

from installer import workflow
from installer.core.checkpoint import read_state, writer_lock
from installer.components.ollama import local_endpoint, proposed_config
from installer.core.process import child_env
from installer.windows.session_process import ProcessTree
from installer.windows.access import check_runtime_access


def inspect_installation(root: Path):
    from installer.operations.maintenance import require_idle
    require_idle(root)
    from installer.operations.uninstall import removal_started
    if removal_started(root): raise ValueError('This installation is being uninstalled and cannot be started; check the result in the external installer or retry.')
    model.guard_path(root)
    state = read_state(root)
    if state['status'] != 'completed' or not state['summary']['workflow_complete']:
        raise ValueError('Installation is incomplete. Finish installation before opening Odessy.')
    plan = state['plan']; paths = workflow.paths_for(root.resolve())
    if Path(plan['install_dir']).resolve() != root.resolve():
        raise ValueError('Installation was moved. Private environments must remain in their original location.')
    endpoint = local_endpoint(plan['endpoint'])
    check_runtime_access(paths['runtime'])
    settings = connection.inspect_application(paths['application'])
    if (Path(settings['runtime_dir']).resolve() != paths['runtime']
        or Path(settings['dependency_dir']).resolve() != paths['dependencies']):
        raise ValueError('Application refers to components outside this installation.')
    report = model.verify_model(paths['models'])
    if plan['model'] != report['model']:
        raise ValueError('Installed model differs from the installation record.')
    values = {}
    model.guard_path(paths['config'])
    for raw in paths['config'].read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'): continue
        if '=' not in line: raise ValueError('Invalid installed connection profile.')
        key, value = line.split('=', 1)
        if key in values: raise ValueError('Duplicate installed profile key.')
        values[key] = value
    if values != proposed_config({'endpoint': endpoint, 'model': report['model'], 'service_status': 'available'}):
        raise ValueError('Installed profile changed; no configuration was overwritten.')
    return dict(paths=paths, settings=settings, model=report, endpoint=endpoint)


def workspace_path(root: Path, workspace: Path):
    model.guard_path(workspace)
    path = workspace.resolve(); installation = root.resolve()
    if not path.is_dir(): raise ValueError('Select an existing work folder.')
    if path == installation or installation in path.parents or path in installation.parents:
        raise ValueError('Choose a work folder separate from the installation, not its parent or child.')
    return path


def session_environment(info, scratch: Path):
    # Explicit local profile; inherited cloud routing/external server settings do not leak in.
    env = {k: v for k, v in child_env(info['paths']['runtime']).items()
           if not k.upper().startswith('ODESSY_') and k.upper() not in {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY'}}
    env.update(ODESSY_CONFIG_FILE=str(info['paths']['config']), ODESSY_SERVERS_FILE=str(scratch / 'no-servers.toml'),
               ODESSY_COMMANDS_FILE=str(scratch / 'no-commands.toml'), NO_PROXY='*', no_proxy='*',
               # Thinking and final output share this ceiling. The Chinese directory
               # request exhausted 1024 thinking tokens; 2048 yielded a valid tool call.
               ODESSY_MODEL_NUM_PREDICT='2048',
               PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    return env


def run_turn(info, workspace, text, env, *, popen=subprocess.Popen, tree_factory=ProcessTree):
    command = [info['settings']['python'], '-I', '-B', str(info['paths']['application'] / 'launch.py'),
               '--workspace', str(workspace), '--', '--model-backend', 'lfm', '--', text]
    process = None; tree = tree_factory()
    try:
        # Inherit the terminal so existing runtime confirmations remain interactive.
        process = popen(command, cwd=workspace, env=env)
        tree.attach(process)
        return process.wait()
    finally:
        tree.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


def run_session(root: Path, workspace: Path, *, text=None, inspect=inspect_installation,
                service=ollama_runtime.managed_service, check_service=model.verify_service,
                turn=run_turn, read=input, tree_factory=ProcessTree, popen=subprocess.Popen):
    ollama_runtime.require_windows()
    workspace = workspace_path(root, workspace)
    # The installation lock is also held by installation/resume: never race a writer.
    if not (root / '.installer').is_dir(): raise ValueError('No installation record found.')
    with writer_lock(root):
        print('Checking installed application and pinned model...', flush=True)
        info = inspect(root)
        sessions = info['paths']['service_state'] / 'launch-sessions'
        model.guard_path(sessions); sessions.mkdir(parents=True, exist_ok=True)
        session_state = sessions / uuid.uuid4().hex
        service_tree = tree_factory()
        def owned_popen(*args, **kwargs):
            process = popen(*args, **kwargs)
            try: service_tree.attach(process)
            except BaseException:
                process.terminate(); process.wait(timeout=5); raise
            return process
        try:
            with service(info['paths']['ollama'], info['paths']['models'], session_state,
                         info['endpoint'], info['model']['model'], popen=owned_popen,tree_factory=None) as current:
                check_service(info['endpoint'], info['model'])
                if not current['reused']: print('Service log:', session_state / 'server.log', flush=True)
                print('Odessy ready. Workspace:', workspace, flush=True)
                print('Existing model service reused.' if current['reused'] else 'Model service started for this session.', flush=True)
                with tempfile.TemporaryDirectory(prefix='odessy-session-') as folder:
                    env = session_environment(info, Path(folder))
                    if text is not None:
                        return turn(info, workspace, text, env)
                    print('Enter a request. /exit quits. Each request is a separate turn; approvals use the terminal.', flush=True)
                    while True:
                        try: request = read('\nOdessy> ')
                        except EOFError: return 0
                        if request.strip().lower() in {'/exit', '/quit'}: return 0
                        if not request.strip(): continue
                        code = turn(info, workspace, request, env)
                        if code: print('Request failed (exit', code, '). You can retry or enter /exit.', flush=True)
        finally:
            service_tree.close()  # Also owns newly spawned Ollama runner descendants; never reused services.
            print('Session ended. Processes started by this session have been stopped.', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--text', help='One request, then stop the owned model service.')
    parser.add_argument('--pause-on-exit', action='store_true', help='Keep a newly opened console readable before closing.')
    args = parser.parse_args(argv)
    code = 1
    try:
        code = run_session(args.install_dir, args.workspace, text=args.text)
    except KeyboardInterrupt:
        print('\nStopped. Session-owned processes cleaned up.'); code = 130
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('Unable to start Odessy:', error, file=sys.stderr)
    if args.pause_on_exit:
        try: input('\nPress Enter to close this terminal...')
        except (EOFError, KeyboardInterrupt): pass
    return code


if __name__ == '__main__': raise SystemExit(main())
