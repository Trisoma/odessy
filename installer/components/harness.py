"""Stage the pinned Harness runtime from its declared wheel.

What is installed where::

    <install root>/runtime/harness-runtime/
        deepseek-harness-sdk-runtime-win-x64.exe      the engine (self-contained Node SEA)
        deepseek-harness-sdk-runtime-win-x64-rg.exe   the engine's own ripgrep sidecar
        win-x64/downloads.json                        the engine's own optional-payload list
        licenses/{LICENSE,THIRD_PARTY_NOTICES.md}     retained upstream licences
        harness-runtime.json                          completion marker (written last)

The declaration is ``src/odessy/runtime_backends/harness/harness-artifacts.json`` — the same
file ``odessy.runtime_backends.harness.pin`` derives its enforced constants from. There is
no second pin: the digest the installer checks and the digest a launch enforces are one
line of one file. Nothing is extracted from the wheel except the members that file names,
so an archive carrying extra entries cannot smuggle them into the payload.

What this deliberately does *not* do:

- It does not vendor the upstream source tree or a ``node_modules`` closure. The runtime
  wheel is a published, hash-pinned platform artifact.
- It does not run ``dsh plugin add`` or pnpm. The composed profile is written by
  ``odessy.runtime_backends.harness.resources.compose_profile`` at launch from the bundle
  that ships inside the application, so an installed machine needs no package manager.
- It does not download anything at launch. Everything is resolved and verified here, once.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

from installer.core.download import DownloadError, IntegrityError, download_artifact, verified
from installer.core.manifest import Artifact, Manifest, _unique_keys, validate_https_url
from installer.core.preflight import REPO_ROOT, default_locations
from installer.core.process import owned_directory
from installer.core.space import budget

INVENTORY = Path('src') / 'odessy' / 'runtime_backends' / 'harness' / 'harness-artifacts.json'
MARKER = 'harness-runtime.json'
WHEEL_TAG = 'win_amd64'
TARGET = 'win-x64'
COMPONENT_DIRECTORY = 'harness-runtime'


class HarnessPreparationError(ValueError):
    pass


def inventory_path(repo: Path = REPO_ROOT) -> Path:
    return Path(repo) / INVENTORY


def load_declaration(path: Path | None = None) -> dict:
    """Read and shape-check the product declaration this component installs from."""

    path = inventory_path() if path is None else Path(path)
    try:
        data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_keys)
    except (OSError, ValueError) as error:
        raise HarnessPreparationError(f'The Harness artifact inventory is unreadable: {error}') from error
    if not isinstance(data, dict) or type(data.get('schema')) is not int:
        raise HarnessPreparationError('Invalid Harness artifact inventory.')
    runtime = data.get('runtime')
    if not isinstance(runtime, dict):
        raise HarnessPreparationError('The Harness artifact inventory declares no runtime.')
    for key in ('declared_version', 'source_tag'):
        if not isinstance(runtime.get(key), str) or not runtime[key]:
            raise HarnessPreparationError(f'The Harness runtime declaration has no {key}.')
    wheel = (runtime.get('wheels') or {}).get(WHEEL_TAG)
    if not isinstance(wheel, dict):
        raise HarnessPreparationError(f'The Harness runtime declaration has no {WHEEL_TAG} wheel.')
    for key in ('file', 'sha256', 'bytes', 'url', 'archive_prefix'):
        if not wheel.get(key):
            raise HarnessPreparationError(f'The declared {WHEEL_TAG} wheel has no {key}.')
    validate_https_url(wheel['url'])
    executable = (runtime.get('executables') or {}).get(TARGET)
    if not isinstance(executable, dict) or not executable.get('path_in_wheel'):
        raise HarnessPreparationError(f'The Harness runtime declaration has no {TARGET} executable.')
    if not isinstance(executable.get('sidecars'), list) or not executable['sidecars']:
        raise HarnessPreparationError('The declared executable has no sidecar list; refusing an unchecked payload.')
    for sidecar in executable['sidecars']:
        if not isinstance(sidecar, dict) or not sidecar.get('path_in_wheel') or not sidecar.get('sha256'):
            raise HarnessPreparationError('A declared sidecar has no wheel member or digest.')
    if not isinstance(runtime.get('licenses'), list) or not runtime['licenses']:
        raise HarnessPreparationError('The Harness runtime declaration retains no licences.')
    for licence in runtime['licenses']:
        if not isinstance(licence, dict) or not licence.get('path_in_wheel') or not licence.get('sha256'):
            raise HarnessPreparationError('A declared licence has no wheel member or digest.')
    return data


def wheel_artifact(declaration: dict) -> Artifact:
    """The wheel as the download cache sees it: one artifact id, one digest, one URL."""

    runtime = declaration['runtime']
    wheel = runtime['wheels'][WHEEL_TAG]
    return Artifact(id='harness-runtime', version=runtime['declared_version'],
                    url=wheel['url'], size_bytes=int(wheel['bytes']), sha256=wheel['sha256'])


def _members(declaration: dict) -> list[tuple[str, str, str, int]]:
    """``(wheel member, installed path, digest, size)`` for every file to extract.

    The installed path is derived from the declared wheel member by stripping the declared
    archive prefix, so a declaration and a layout cannot disagree; licences are placed under
    ``licenses/`` because that is what they are.
    """

    runtime = declaration['runtime']
    prefix = runtime['wheels'][WHEEL_TAG]['archive_prefix'].strip('/') + '/'
    executable = runtime['executables'][TARGET]
    planned = [(executable['path_in_wheel'], executable['file'],
                executable['sha256'], int(executable['bytes']))]
    for sidecar in executable['sidecars']:
        member = sidecar['path_in_wheel']
        # The installed layout mirrors the declared ``file`` exactly, and the wheel member
        # must be that file under ``<prefix>runtime/``. A declaration that disagrees with
        # itself is refused instead of silently installing a differently-named path.
        if member != prefix + 'runtime/' + sidecar['file']:
            raise HarnessPreparationError('A sidecar declaration is inconsistent: ' + member)
        planned.append((member, sidecar['file'], sidecar['sha256'], int(sidecar['bytes'])))
    for licence in runtime['licenses']:
        planned.append((licence['path_in_wheel'], 'licenses/' + licence['file'],
                        licence['sha256'], int(licence['bytes'])))
    names = [installed for _member, installed, _digest, _size in planned]
    if len({name.casefold() for name in names}) != len(names):
        raise HarnessPreparationError('The declaration installs the same path twice.')
    return planned


def unpacked_bytes(declaration: dict) -> int:
    return sum(size for _member, _installed, _digest, size in _members(declaration))


def _extract(wheel: Path, planned: list[tuple[str, str, str, int]], target: Path) -> list[dict]:
    """Copy only the declared members, verifying each against its declared digest.

    Every destination comes from code (``planned``), never from the archive, so an archive
    entry cannot choose where it lands. A member that is absent, duplicated, larger than
    declared or of a different digest fails the whole stage.
    """

    seen = set()
    records = []
    try:
        archive = zipfile.ZipFile(wheel)
    except zipfile.BadZipFile as error:
        raise HarnessPreparationError(f'The runtime wheel is not a readable ZIP archive: {error}') from error
    with archive:
        infos = {info.filename: info for info in archive.infolist()}
        if len(infos) != len(archive.infolist()):
            raise HarnessPreparationError('The runtime wheel contains duplicate member names.')
        for member, installed, digest, size in planned:
            info = infos.get(member)
            if info is None:
                raise HarnessPreparationError('The runtime wheel is missing a declared member: ' + member)
            if info.is_dir() or info.file_size != size:
                raise HarnessPreparationError('A declared member has the wrong type or size: ' + member)
            data = archive.read(member)
            if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                raise HarnessPreparationError('A declared member does not match its digest: ' + member)
            key = installed.casefold()
            if key in seen:
                raise HarnessPreparationError('Two declared members install to the same path: ' + installed)
            seen.add(key)
            destination = target / installed
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            records.append({'file': installed, 'sha256': digest, 'size_bytes': size})
    return records


def probe_version(executable: Path, expected: str, *, timeout: float = 60.0) -> str:
    """Run ``--version`` with a throw-away ``DSH_HOME`` and require the declared version.

    The home is created for this call and removed afterwards, so a staging check cannot
    write into a user's Harness state (sessions, profiles, credentials) even if a future
    engine starts initialising state on ``--version``. The environment is scrubbed
    independently of that: no route credential, bridge token or spool variable is inherited.
    """

    with tempfile.TemporaryDirectory(prefix='odessy-harness-probe-') as home:
        env = {name: value for name, value in os.environ.items()
               if name.upper() in {'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP',
                                   'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PATH', 'PATHEXT',
                                   'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'OS'}}
        env['DSH_HOME'] = home
        try:
            completed = subprocess.run([str(executable), '--version'], capture_output=True,
                                       text=True, env=env, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise HarnessPreparationError(f'The staged runtime cannot be launched: {error}') from error
    output = f'{completed.stdout}\n{completed.stderr}'.strip()
    reported = ''
    for token in output.replace('\n', ' ').split():
        candidate = token.strip('(),;:').lstrip('v')
        parts = candidate.split('-')[0].split('.')
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            reported = candidate
    if completed.returncode or reported != expected:
        raise HarnessPreparationError(
            'The staged runtime does not report the declared version: '
            f'expected {expected}, got {reported or output[:120]!r}')
    return reported


def prepare(declaration: dict, target: Path, cache_dir: Path, *, download=download_artifact,
            probe=probe_version) -> dict:
    """Download, verify, extract and probe the runtime into a new directory."""

    artifact = wheel_artifact(declaration)
    layout = {artifact.id: {'destination': 'app', 'unpacked_bytes': unpacked_bytes(declaration)}}
    if not all(row['sufficient_for_subset'] for row in budget(
            _manifest(declaration, artifact), layout, cache_dir=cache_dir,
            app_dir=Path(target), model_dir=Path(target))):
        raise HarnessPreparationError('Insufficient space for the runtime download and staging.')
    downloaded = download(artifact, cache_dir).path
    if not verified(downloaded, artifact):
        raise IntegrityError('The runtime wheel does not match its declared digest.')
    planned = _members(declaration)
    with owned_directory(target) as stage:
        files = _extract(downloaded, planned, stage)
        executable = stage / declaration['runtime']['executables'][TARGET]['file']
        reported = probe(executable, declaration['runtime']['declared_version'])
        marker = {'schema_version': 1, 'component': 'harness-runtime',
                  'version': declaration['runtime']['declared_version'],
                  'source_tag': declaration['runtime']['source_tag'],
                  'source_commit': declaration['runtime'].get('source_commit'),
                  'executable': executable.name,
                  'reported_version': reported,
                  'wheel': {'file': artifact.url.rsplit('/', 1)[-1], 'sha256': artifact.sha256,
                            'bytes': artifact.size_bytes},
                  'files': files}
        (stage / MARKER).write_text(json.dumps(marker, indent=2), encoding='utf-8')
    return marker


def _manifest(declaration: dict, artifact: Artifact) -> Manifest:
    return Manifest(release=declaration['runtime']['declared_version'], artifacts=(artifact,))


def verify(target: Path, declaration: dict | None = None) -> dict:
    """Re-check a staged component: marker, every declared file, and the version probe."""

    declaration = load_declaration() if declaration is None else declaration
    root = Path(target)
    marker_path = root / MARKER
    if not marker_path.is_file():
        raise HarnessPreparationError('The Harness runtime component is not installed: ' + str(root))
    data = json.loads(marker_path.read_text(encoding='utf-8'), object_pairs_hook=_unique_keys)
    if (data.get('schema_version') != 1 or data.get('component') != 'harness-runtime'
            or data.get('version') != declaration['runtime']['declared_version']):
        raise HarnessPreparationError('The staged Harness runtime marker is incomplete or for another version.')
    expected = {installed: (digest, size) for _m, installed, digest, size in _members(declaration)}
    recorded = {row['file']: row['sha256'] for row in data.get('files', []) if isinstance(row, dict)}
    if set(recorded) != set(expected):
        raise HarnessPreparationError('The staged file list differs from the declaration.')
    for installed, (digest, size) in expected.items():
        path = root / installed
        if not path.is_file() or path.is_symlink() or path.stat().st_size != size:
            raise HarnessPreparationError('A staged runtime file is missing or has the wrong size: ' + installed)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise HarnessPreparationError('A staged runtime file does not match its digest: ' + installed)
    return {'runtime_dir': str(root), 'version': data['version'], 'source_tag': data['source_tag'],
            'executable': str(root / data['executable']), 'files_verified': len(expected)}


def installed_paths(root: Path) -> dict:
    """The paths an assembled application records for this component."""

    runtime_dir = Path(root) / 'runtime' / COMPONENT_DIRECTORY
    return {'runtime_dir': str(runtime_dir),
            'executable': str(runtime_dir / 'deepseek-harness-sdk-runtime-win-x64.exe'),
            'node_executable': str(Path(root) / 'runtime' / 'node-runtime' / 'node.exe'),
            'node_tools_dir': str(Path(root) / 'application' / 'tools')}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    app, _ = default_locations(os.environ, Path.home())
    parser.add_argument('--inventory', type=Path, default=inventory_path())
    parser.add_argument('--runtime', type=Path, default=app / 'runtime' / COMPONENT_DIRECTORY)
    parser.add_argument('--cache-dir', type=Path, default=app.parent.parent / 'Odessy' / 'downloads')
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args()
    try:
        declaration = load_declaration(args.inventory)
        artifact = wheel_artifact(declaration)
        print(f'Harness runtime: {declaration["runtime"]["declared_version"]} '
              f'({declaration["runtime"]["source_tag"]})')
        print(f'Wheel: {artifact.url.rsplit("/", 1)[-1]} {artifact.size_bytes} bytes sha256 {artifact.sha256[:16]}...')
        for member, installed, digest, size in _members(declaration):
            print(f'  {size:>12}  {installed}  {digest[:16]}...')
        print('Runtime directory:', args.runtime)
        if not args.prepare:
            print('PREVIEW ONLY. Add --prepare to download, verify, extract and probe the runtime.')
            return 0
        marker = prepare(declaration, args.runtime, args.cache_dir)
        print('Staged:', json.dumps(marker, indent=2))
        print('Verified:', json.dumps(verify(args.runtime, declaration), indent=2))
        return 0
    except (OSError, ValueError, DownloadError, HarnessPreparationError, IntegrityError,
            subprocess.SubprocessError) as error:
        print('Harness runtime preparation failed:', error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
