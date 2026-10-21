"""Developer installation workflow: preview, prepare, checkpoints and verified retry."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
import uuid
from pathlib import Path

from installer import application, connection, dependencies, model, ollama_runtime, runtime
from installer.checkpoint import Step, fingerprint, read_state, run_steps, state_path
from installer.download import DownloadError, _reject_link, download_artifact
from installer.ollama import local_endpoint, proposed_config, write_config
from installer.preflight import REPO_ROOT, default_locations
from installer.process import validate_runtimes
from installer.space import RESERVE_BYTES, load_resources, volume_for

STEP_NAMES = ('tool_bundle', 'runtime', 'dependencies', 'application', 'ollama_runtime', 'model', 'connection', 'configuration')


def paths_for(root: Path) -> dict[str, Path]:
    return dict(root=root, runtime=root / 'runtime', dependencies=root / 'dependencies', application=root / 'application',
                ollama=root / 'ollama-runtime', models=root / 'models', service_state=root / 'ollama-state',
                config=root / 'config/odessy.env')


def release_inputs(bundle: Path):
    _, tool = application.validate_bundle(bundle)
    runtime_manifest, runtime_resources = runtime.load_release(runtime.DEFAULT_RESOURCES)
    deps_manifest, deps_resources = load_resources(dependencies.DEFAULT_RESOURCES)
    ollama_manifest, ollama_resources = ollama_runtime.load_release()
    model_manifest, model_resources, _ = model.load_release()
    source = application.source_snapshot(REPO_ROOT)
    files = {}
    installer = Path(__file__).resolve().parent
    selected = list(installer.glob('*.py')) + list((installer / 'templates').glob('*.py'))
    selected += [p for p in (installer / 'releases').rglob('*') if p.is_file() and p.suffix in {'.json', '.txt'}]
    for path in sorted(selected):
        files[path.relative_to(installer).as_posix()] = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    for name, data in source.items():
        files['application/' + name] = hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()
    release_hash = fingerprint({'files': files, 'tool_archive_sha256': tool['sha256']})
    cache_bytes = sum(m.download_bytes for m in (runtime_manifest, deps_manifest, ollama_manifest, model_manifest))
    # The dependency installer temporarily needs a second wheel copy for offline pip.
    cache_bytes += deps_manifest.download_bytes
    app_bytes = (sum(s['unpacked_bytes'] for s in runtime_resources['runtimes'].values())
                 + sum(s['unpacked_bytes'] for s in deps_resources['layout'].values())
                 + ollama_resources['unpacked_bytes'] + tool['unpacked_bytes'] + sum(map(len, source.values())))
    amounts = dict(cache=cache_bytes, application=2 * app_bytes, models=2 * model_manifest.download_bytes)
    return release_hash, model_resources['model'], amounts


def build_plan(root: Path, cache: Path, bundle: Path, endpoint: str, timeout: int) -> dict:
    for path in (root, cache, bundle):
        model.guard_path(path)
    root = root.resolve(); cache = cache.resolve(); bundle = bundle.resolve()
    protected = [REPO_ROOT / name for name in ('installer', 'src', 'tools', 'config', 'policy', 'THIRD-PARTY-LICENSES')]
    if any(root == path or path in root.parents for path in protected):
        raise ValueError('Do not install generated components inside project source/configuration directories.')
    if root == REPO_ROOT or root in REPO_ROOT.parents or root == cache or root in cache.parents or cache in root.parents:
        raise ValueError('Use a dedicated install root separate from the source repository root and download cache.')
    if root in bundle.parents or root == bundle:
        raise ValueError('Prebuilt input bundle must be outside the new installation root.')
    if type(timeout) is not int or not 15 <= timeout <= 600:
        raise ValueError('Model check timeout must be from 15 to 600 seconds.')
    release_hash, model_name, amounts = release_inputs(bundle)
    return dict(schema_version=1, install_dir=str(root), cache_dir=str(cache), tool_bundle=str(bundle),
                endpoint=local_endpoint(endpoint), timeout=timeout, release_sha256=release_hash,
                model=model_name, budget_bytes=amounts)


def combined_budget(plan: dict, *, probe=volume_for) -> list[dict]:
    root = Path(plan['install_dir']); destinations = dict(cache=Path(plan['cache_dir']), application=root, models=root / 'models')
    groups = {}
    for name, amount in plan['budget_bytes'].items():
        volume = probe(destinations[name])
        group = groups.setdefault(volume.id, dict(volume=volume.id, free_bytes=volume.free_bytes, payload_bytes=0, contributions={}))
        group['free_bytes'] = min(group['free_bytes'], volume.free_bytes)
        group['payload_bytes'] += amount; group['contributions'][name] = amount
    for group in groups.values():
        group['reserve_bytes'] = max(RESERVE_BYTES, (group['payload_bytes'] + 9) // 10)
        group['required_bytes'] = group['payload_bytes'] + group['reserve_bytes']
        group['sufficient'] = group['required_bytes'] <= group['free_bytes']
    return list(groups.values())


def existing_directory(path: Path) -> bool:
    model.guard_path(path)
    if not path.exists():
        return False
    if not path.is_dir() or (path / '.odessy-preparing').exists():
        raise ValueError('Incomplete or invalid component was preserved: ' + str(path))
    return True


def steps_for(plan: dict, *, emit_progress=print, cancelled=lambda: False) -> list[Step]:
    paths = paths_for(Path(plan['install_dir'])); cache = Path(plan['cache_dir']); bundle = Path(plan['tool_bundle'])
    def fetch(artifact, cache):
        if cancelled():
            raise KeyboardInterrupt()
        previous = [-1]
        def progress(name, received, total):
            if cancelled():
                raise KeyboardInterrupt()
            percent = received * 100 // total
            if percent // 5 != previous[0] // 5 or received == total:
                emit_progress(f'[DOWNLOAD] {name}: {percent}% ({received}/{total} bytes)')
                previous[0] = percent
        return download_artifact(artifact, cache, progress=progress)
    def bundle_check():
        _, data = application.validate_bundle(bundle)
        return {'archive_sha256': data['sha256'], 'precompiled': True}
    def runtime_check():
        if not existing_directory(paths['runtime']): return None
        marker = json.loads((paths['runtime'] / 'odessy-runtime.json').read_text(encoding='utf-8'))
        versions = validate_runtimes(paths['runtime'])
        if marker != {'schema_version': 1, 'versions': versions}:
            raise ValueError('Runtime marker differs from actual private versions.')
        return versions
    def runtime_prepare():
        manifest, resources = runtime.load_release(runtime.DEFAULT_RESOURCES)
        return runtime.prepare_runtime(manifest, resources, paths['runtime'], cache, download=fetch)
    def dependency_check():
        if not existing_directory(paths['dependencies']): return None
        return application.validate_dependencies(paths['runtime'], paths['dependencies'])
    def application_check():
        if not existing_directory(paths['application']): return None
        settings = connection.inspect_application(paths['application'])
        if (Path(settings['runtime_dir']).resolve() != paths['runtime'].resolve()
            or Path(settings['dependency_dir']).resolve() != paths['dependencies'].resolve()):
            raise ValueError('Application is bound to other components; existing output is preserved.')
        expected = {name: application.text_hash(data) for name, data in application.source_snapshot(REPO_ROOT).items()}
        if settings['source_sha256'] != expected:
            raise ValueError('Application source differs from this installation release.')
        return {'app_dir': str(paths['application']), 'python': settings['python']}
    def ollama_check():
        if not existing_directory(paths['ollama']): return None
        manifest, resources = ollama_runtime.load_release()
        ollama_runtime.validate_installation(paths['ollama'], manifest, resources)
        return {'version': manifest.artifacts[0].version}
    def model_check():
        _, resources, _ = model.load_release()
        if not model.manifest_path(paths['models'], resources['model']).exists():
            return None  # Verified orphan blobs from an interrupted download can be reused.
        return model.verify_model(paths['models'])
    def connection_check():
        if cancelled(): raise KeyboardInterrupt()
        # A killed installer cannot run managed_service's lock cleanup. Keep each
        # verification attempt separate, just like installed launch sessions, so
        # abandoned locks/logs are preserved without blocking a verified retry.
        sessions = paths['service_state'] / 'verification-sessions'
        model.guard_path(sessions)
        sessions.mkdir(parents=True, exist_ok=True)
        session_state = sessions / uuid.uuid4().hex
        with ollama_runtime.managed_service(paths['ollama'], paths['models'], session_state,
                plan['endpoint'], plan['model']) as session:
            if cancelled(): raise KeyboardInterrupt()
            if not session['reused']:
                emit_progress(f'[CHECK] Service log: {session_state / "server.log"}')
            report = connection.verify_connection(paths['application'], paths['models'], plan['endpoint'],
                timeout=plan['timeout'], cancelled=cancelled)
            report['service_reused'] = session['reused']
            report['service_state_dir'] = None if session['reused'] else str(session_state)
        report['owned_service_stopped'] = not session['reused']
        return report
    config_report = {'service_status': 'available', 'endpoint': plan['endpoint'], 'model': plan['model']}
    def config_check():
        path = paths['config']; model.guard_path(path)
        if not path.exists(): return None
        values = {}
        for raw in path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#'): continue
            if '=' not in line: raise ValueError('Existing workflow profile is invalid; it was preserved.')
            key, value = line.split('=', 1)
            if key in values: raise ValueError('Duplicate profile key; existing profile preserved.')
            values[key] = value
        if values != proposed_config(config_report):
            raise ValueError('Existing workflow profile differs from the plan; it was preserved.')
        return {'config_file': str(path)}
    def config_prepare():
        write_config(paths['config'], config_report)
        return {'config_file': str(paths['config'])}
    return [
        Step('tool_bundle', bundle_check, bundle_check),
        Step('runtime', runtime_prepare, runtime_check),
        Step('dependencies', lambda: dependencies.prepare_dependencies(paths['runtime'], paths['dependencies'], cache, download=fetch), dependency_check),
        Step('application', lambda: application.assemble(paths['runtime'], paths['dependencies'], bundle, paths['application']), application_check),
        Step('ollama_runtime', lambda: ollama_runtime.prepare_runtime(paths['ollama'], cache, download=fetch), ollama_check),
        Step('model', lambda: model.prepare_model(paths['models'], cache, download=fetch), model_check),
        Step('connection', connection_check, lambda: None, always_run=True),
        Step('configuration', config_prepare, config_check),
    ]


def execute(plan: dict, *, resume=False, emit=lambda event: None, cancelled=lambda: False) -> dict:
    from installer.maintenance import require_idle
    require_idle(Path(plan['install_dir']))
    from installer.uninstall import removal_started
    if removal_started(Path(plan['install_dir'])): raise ValueError('This installation has been uninstalled; choose a new directory to install.')
    ollama_runtime.require_windows()
    expected = build_plan(Path(plan['install_dir']), Path(plan['cache_dir']), Path(plan['tool_bundle']), plan['endpoint'], plan['timeout'])
    if expected != plan:
        raise ValueError('Installation release inputs changed after preview; rebuild the plan before proceeding.')
    if not all(group['sufficient'] for group in combined_budget(plan)):
        raise ValueError('Combined installation disk budget failed; no workflow was started.')
    return run_steps(Path(plan['install_dir']), plan, steps_for(plan, cancelled=cancelled), resume=resume, emit=emit,
                     mutable_options=('timeout',), cancelled=cancelled)


def main(argv=None):
    app, _ = default_locations(os.environ, Path.home())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', type=Path, default=app / 'installation')
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--tool-bundle', type=Path, help='Reviewed local prebuilt bundle (not an npm build).')
    parser.add_argument('--endpoint', help='Loopback service address; new workflows default to port 11435.')
    parser.add_argument('--timeout', type=int, help='Real model per-turn deadline; new workflows default to 180 seconds.')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--run', action='store_true')
    actions.add_argument('--resume', action='store_true')
    actions.add_argument('--status', action='store_true')
    args = parser.parse_args(argv)
    try:
        model.guard_path(args.install_dir)
        if args.status:
            state = read_state(args.install_dir)
            summary = dict(install_dir=state['plan'].get('install_dir', str(args.install_dir)), status=state['status'],
                           **state['summary'], stages=[{key: step[key] for key in ('name', 'status', 'attempts', 'action', 'error')}
                                                      for step in state['steps']])
            print(json.dumps(summary, indent=2)); return 0
        previous = read_state(args.install_dir)['plan'] if args.resume else {}
        bundle = args.tool_bundle or (Path(previous['tool_bundle']) if previous else None)
        if bundle is None:
            raise ValueError('Provide --tool-bundle with the reviewed prebuilt Windows artifact.')
        cache = args.cache_dir or (Path(previous['cache_dir']) if previous else app.parent.parent / 'Odessy/downloads')
        plan = build_plan(args.install_dir, cache, bundle, args.endpoint or previous.get('endpoint', 'http://127.0.0.1:11435'),
                          args.timeout if args.timeout is not None else previous.get('timeout', 180))
        print('Installation root:', plan['install_dir'])
        print('Service endpoint:', plan['endpoint'], '(owned service is stopped after verification)')
        print('Stages:', ' -> '.join(STEP_NAMES))
        volumes = combined_budget(plan)
        for group in volumes:
            print(f'[{"OK" if group["sufficient"] else "FAIL"}] required {group["required_bytes"]/1024**3:.2f} GiB; available {group["free_bytes"]/1024**3:.2f} GiB')
        if not args.run and not args.resume:
            print('PREVIEW ONLY. --run starts preparation, model inference and new profile creation.')
            print('No download, service start, state file or configuration write performed.'); return 0 if all(v['sufficient'] for v in volumes) else 1
        def event(item):
            print(f'[{item["index"]}/{item["total"]}] {item["step"]}: {item["status"]}', flush=True)
        state = execute(plan, resume=args.resume, emit=event)
        print(json.dumps(state['summary'], indent=2))
        print('Checkpoint:', state_path(Path(plan['install_dir'])))
        print('Connection profile:', paths_for(Path(plan['install_dir']))['config'])
        print('Workflow verification completed. Graphical bootstrap, public release delivery and clean-machine qualification remain pending.')
        return 0
    except KeyboardInterrupt:
        print('Cancelled. Checkpoint retained; use --status, then --resume to revalidate and retry.')
        return 130
    except (OSError, ValueError, DownloadError, subprocess.SubprocessError) as error:
        print('Installation workflow failed:', error)
        print('Existing components preserved. Use --status for a recorded stage failure; --resume revalidates outputs before retry.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
