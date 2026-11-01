"""Prepare a pinned private Ollama CLI and supervise only a process we start."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import json
import os
import platform
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from installer.core.download import DownloadError, download_artifact
from installer.core.manifest import _unique_keys, load_manifest, validate_https_url
from installer.components.ollama import inspect_service, local_endpoint
from installer.core.preflight import REPO_ROOT, default_locations, repository_defaults
from installer.core.process import owned_directory
from installer.components.runtime import destination
from installer.core.space import _verified_local, budget
from installer.core.unpack import unpack
from installer.windows.session_process import ProcessTree

DEFAULT_RESOURCES = Path(__file__).resolve().parents[1] / 'releases/windows-x64-ollama/ollama.resources.json'
INSTALL_MARKER = 'odessy-ollama-runtime.json'
STATE_MARKER = 'odessy-ollama-state.json'


def require_windows():
    machine = os.environ.get('PROCESSOR_ARCHITEW6432') or platform.machine()
    if platform.system() != 'Windows' or machine.lower() not in {'amd64', 'x86_64'}:
        raise ValueError('Private Ollama preparation and startup require Windows x64.')
    if sys.getwindowsversion().build < 19045:
        raise ValueError('This release requires Windows 10 22H2 (build 19045) or newer.')


def load_release(path: Path = DEFAULT_RESOURCES):
    data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_keys)
    fields = {'schema_version', 'platform', 'download_manifest', 'download_manifest_sha256',
              'unpacked_bytes', 'files', 'sources'}
    if not isinstance(data, dict) or set(data) != fields or type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise ValueError('Invalid Ollama resource metadata.')
    if data['platform'] != 'windows-x64':
        raise ValueError('Ollama resource platform must be windows-x64.')
    manifest = load_manifest(_verified_local(path.parent, data['download_manifest'], data['download_manifest_sha256']))
    if len(manifest.artifacts) != 1 or manifest.artifacts[0].id != 'ollama-runtime':
        raise ValueError('Exactly one Ollama runtime artifact is required.')
    artifact = manifest.artifacts[0]
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', artifact.version):
        raise ValueError('An exact stable Ollama version is required.')
    expected = f'https://github.com/ollama/ollama/releases/download/v{artifact.version}/ollama-windows-amd64.zip'
    if artifact.url != expected:
        raise ValueError('Use the versioned official Windows x64 CLI archive.')
    for key in ('unpacked_bytes', 'files'):
        if type(data[key]) is not int or data[key] <= 0:
            raise ValueError('Measured Ollama archive sizes and file count are required.')
    if not isinstance(data['sources'], dict) or set(data['sources']) != {'release', 'checksum', 'layout'}:
        raise ValueError('Official metadata source references are required.')
    validate_https_url(data['sources']['release']); validate_https_url(data['sources']['checksum'])
    return manifest, data


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(data)
    return checksum.hexdigest()


def snapshot(root: Path) -> dict:
    root_info = root.lstat()
    if stat.S_ISLNK(root_info.st_mode) or getattr(root_info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Private Ollama CLI root must not be a link or reparse point.')
    result = {}
    for path in root.rglob('*'):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Private Ollama runtime must not contain links or reparse points.')
        if path.is_file() and path.name != INSTALL_MARKER:
            result[path.relative_to(root).as_posix()] = {'size': info.st_size, 'sha256': digest(path)}
    if 'ollama.exe' not in result:
        raise ValueError('Official Ollama CLI executable is missing.')
    return result


def service_env(endpoint: str, models: Path, home: Path) -> dict:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('OLLAMA_')}
    # These assignments affect this child only, including Ollama's identity keys.
    env.update(OLLAMA_HOST=local_endpoint(endpoint), OLLAMA_MODELS=str(models), OLLAMA_NO_CLOUD='1',
               USERPROFILE=str(home), HOME=str(home))
    return env


def probe_version(root: Path, version: str):
    with tempfile.TemporaryDirectory(prefix='odessy-ollama-version-') as scratch:
        home = Path(scratch)
        result = subprocess.run([str(root / 'ollama.exe'), '--version'], cwd=root,
            env=service_env('http://127.0.0.1:11434', home / 'models', home),
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    versions = re.findall(r'version(?: is)?\s+([0-9]+\.[0-9]+\.[0-9]+)', result.stdout + result.stderr)
    if result.returncode or version not in versions:
        raise ValueError('Private Ollama version probe failed: ' + (result.stdout + result.stderr)[-1000:])


def runtime_budget(manifest, resources, target, cache):
    return budget(manifest, {'ollama-runtime': {'destination': 'app', 'unpacked_bytes': resources['unpacked_bytes']}},
                  cache_dir=cache, app_dir=target, model_dir=target)


def prepare_runtime(target: Path, cache: Path, *, resources_path=DEFAULT_RESOURCES,
                    download=download_artifact, probe=probe_version):
    require_windows()
    manifest, resources = load_release(resources_path)
    target = destination(target)
    if not all(v['sufficient_for_subset'] for v in runtime_budget(manifest, resources, target, cache)):
        raise ValueError('Insufficient space for the archive, expanded runtime, staging and reserve.')
    artifact = manifest.artifacts[0]
    print('Preparing verified Ollama download...', flush=True)
    archive = download(artifact, cache).path
    # Some Windows hosts deny moving directories containing the official libraries.
    # Prepare in a new owned final directory; no existing install is replaced.
    with owned_directory(target) as output:
        cli = output / 'cli'
        print('Extracting verified Ollama CLI and libraries...', flush=True)
        unpack(archive, artifact, cli, kind='zip', prefix='', unpacked_bytes=resources['unpacked_bytes'])
        files = snapshot(cli)
        if len(files) != resources['files']:
            raise ValueError('Ollama expanded file count differs from the release metadata.')
        probe(cli, artifact.version)
        marker = dict(schema_version=1, version=artifact.version, archive_sha256=artifact.sha256, files=files)
        (output / INSTALL_MARKER).write_text(json.dumps(marker, indent=2), encoding='utf-8')
    return marker


def validate_installation(root: Path, manifest, resources):
    if (root / '.odessy-preparing').exists():
        raise ValueError('Private Ollama preparation has not completed.')
    info = root.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Private runtime root must not be a link or reparse point.')
    marker = json.loads((root / INSTALL_MARKER).read_text(encoding='utf-8'), object_pairs_hook=_unique_keys)
    artifact = manifest.artifacts[0]
    if (not isinstance(marker, dict) or set(marker) != {'schema_version', 'version', 'archive_sha256', 'files'}
        or type(marker['schema_version']) is not int or marker['schema_version'] != 1
        or marker['version'] != artifact.version or marker['archive_sha256'] != artifact.sha256):
        raise ValueError('Private Ollama installation does not match the selected release.')
    files = snapshot(root / 'cli')
    if (files != marker['files'] or len(files) != resources['files']
        or sum(item['size'] for item in files.values()) != resources['unpacked_bytes']):
        raise ValueError('Private Ollama files changed or are incomplete; prepare a new directory.')


def check_directories(runtime: Path, models: Path, state: Path):
    for path in (runtime, models, state):
        if not path.is_absolute():
            raise ValueError('Runtime, models and state directories must be absolute.')
    paths = [p.resolve() for p in (runtime, models, state)]
    for i, path in enumerate(paths):
        for other in paths[i + 1:]:
            if path == other or path in other.parents or other in path.parents:
                raise ValueError('Runtime, model and state directories must be separate, without nesting.')


@contextlib.contextmanager
def managed_service(runtime: Path, models: Path, state: Path, endpoint: str, model: str, *,
                    resources_path=DEFAULT_RESOURCES, popen=subprocess.Popen,
                    inspect=inspect_service, validate=validate_installation, startup_timeout=30,
                    tree_factory=ProcessTree):
    require_windows()
    endpoint = local_endpoint(endpoint)
    check_directories(runtime, models, state)
    existing = inspect(endpoint, model, timeout=1)
    if existing['service_status'] == 'available':
        yield {'reused': True, 'report': existing, 'process': None}
        return
    # An occupied non-Ollama port must never be taken over or stopped.
    url = urlsplit(endpoint)
    family = socket.AF_INET6 if ':' in url.hostname else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            # local_endpoint already rejects non-loopback endpoints. Keep the
            # bind target explicit as well, never allowing None or an empty host.
            bind_host = url.hostname or ('::1' if family == socket.AF_INET6 else '127.0.0.1')
            probe.bind((bind_host, url.port))
        except OSError as error:
            raise ValueError('The endpoint port is occupied; preserve it and choose another --endpoint.') from error
    manifest, resources = load_release(resources_path)
    validate(runtime, manifest, resources)
    state_identity = {'schema_version': 1, 'runtime_dir': str(runtime.resolve())}
    if state.exists():
        if (state.is_symlink() or getattr(state.lstat(), 'st_file_attributes', 0) & 0x400
            or json.loads((state / STATE_MARKER).read_text(encoding='utf-8')) != state_identity):
            raise ValueError('Existing state directory is not owned by this runtime.')
    else:
        with owned_directory(state) as output:
            (output / STATE_MARKER).write_text(json.dumps(state_identity), encoding='utf-8')
    # A live session owns this lock; stale locks are not removed automatically.
    lock = state / 'serve.lock'
    try:
        with lock.open('x', encoding='utf-8') as stream:
            stream.write(str(os.getpid()))
    except FileExistsError as error:
        raise ValueError(f'State session lock already exists: {lock}. Preserve the other session or choose a new --state-dir.') from error
    process = None;tree = None
    try:
        # Callers that already attach their children to an owned job pass None.
        # Reused services return above and never enter an owned process tree.
        if tree_factory is not None:tree=tree_factory()
        models.mkdir(parents=True, exist_ok=True)
        home = state / 'home'; home.mkdir(exist_ok=True)
        with (state / 'server.log').open('ab') as log:
            process = popen([str(runtime / 'cli/ollama.exe'), 'serve'], cwd=runtime / 'cli',
                env=service_env(endpoint, models, home), stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if tree is not None:tree.attach(process)
            deadline = time.monotonic() + startup_timeout
            while True:
                if process.poll() is not None:
                    raise ValueError(f'Private Ollama exited before readiness; inspect {state / "server.log"}.')
                report = inspect(endpoint, model, timeout=0.5)
                if report['service_status'] == 'available' and process.poll() is None:
                    if report['version'] != manifest.artifacts[0].version:
                        raise ValueError('Service version differs from the private release; possible endpoint conflict.')
                    break
                if time.monotonic() >= deadline:
                    raise ValueError(f'Private Ollama startup timed out; inspect {state / "server.log"}.')
                time.sleep(0.2)
            yield {'reused': False, 'report': report, 'process': process}
    finally:
        try:
            # Use the live Popen handle, never a remembered PID or process name.
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
        finally:
            try:
                if tree is not None:tree.close()
            finally:lock.unlink()


def main(argv=None):
    app, default_models = default_locations(os.environ, Path.home())
    defaults = repository_defaults(REPO_ROOT / 'config/odessy.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', type=Path, default=app / 'ollama-runtime')
    parser.add_argument('--model-dir', type=Path, default=default_models)
    parser.add_argument('--state-dir', type=Path, default=app / 'ollama-state')
    parser.add_argument('--cache-dir', type=Path, default=app.parent.parent / 'Odessy/downloads')
    parser.add_argument('--endpoint', default=defaults['ODESSY_MODEL_ENDPOINT'])
    parser.add_argument('--model', default=defaults['ODESSY_MODEL_NAME'])
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--prepare', action='store_true', help='Download and prepare a new private CLI directory.')
    action.add_argument('--serve', action='store_true', help='Supervise the private local service until Ctrl+C.')
    args = parser.parse_args(argv)
    try:
        endpoint = local_endpoint(args.endpoint)
        check_directories(args.runtime_dir, args.model_dir, args.state_dir)
        if args.serve:
            with managed_service(args.runtime_dir, args.model_dir, args.state_dir, endpoint, args.model) as session:
                if session['reused']:
                    print('Existing Ollama service is available; no process started or stopped.')
                else:
                    print('Private Ollama ready:', endpoint, flush=True)
                    print('Models:', args.model_dir, '\nLog:', args.state_dir / 'server.log', flush=True)
                    print('Keep this terminal open. Ctrl+C stops only this managed process.', flush=True)
                    session['process'].wait()
                    raise ValueError('Private Ollama stopped unexpectedly; inspect its log.')
            print('No model download or inference performed. Use installer.ollama to inspect the service.')
            return 0
        manifest, resources = load_release()
        target = destination(args.runtime_dir)
        volumes = runtime_budget(manifest, resources, target, args.cache_dir)
        artifact = manifest.artifacts[0]
        print(f'Ollama {artifact.version}: {artifact.size_bytes / 1024**3:.2f} GiB download; {resources["unpacked_bytes"] / 1024**3:.2f} GiB expanded.')
        print('Private runtime directory:', target)
        for volume in volumes:
            status = 'OK' if volume['sufficient_for_subset'] else 'FAIL'
            print(f'[{status}] required {volume["required_bytes"] / 1024**3:.2f} GiB; available {volume["free_bytes"] / 1024**3:.2f} GiB')
        print('Runtime-only budget; model download and size are still pending.')
        if not all(v['sufficient_for_subset'] for v in volumes):
            return 1
        if not args.prepare:
            print('PREVIEW ONLY. --prepare downloads the pinned official archive; --serve is a separate action.')
            return 0
        last = [-1]
        def fetch(artifact, cache):
            def progress(name, received, total):
                percent = received * 100 // total
                if percent // 5 != last[0] // 5 or received == total:
                    print(f'[DOWNLOAD] Ollama: {percent}% ({received}/{total} bytes)', flush=True)
                    last[0] = percent
            return download_artifact(artifact, cache, progress=progress)
        prepare_runtime(target, args.cache_dir, download=fetch)
        print('Private Ollama CLI ready. Service not started; no models downloaded; system PATH unchanged.')
        return 0
    except KeyboardInterrupt:
        print('Cancelled. Owned process/staging cleaned; prepared runtime, models and logs preserved.')
        return 130
    except (OSError, ValueError, DownloadError, subprocess.SubprocessError) as error:
        print('Ollama runtime stage failed:', error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
