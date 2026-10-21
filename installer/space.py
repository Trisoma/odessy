"""Read-only disk budget for measured release resources, grouped by volume."""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from installer.manifest import ManifestError, load_manifest, _unique_keys
from installer.preflight import REPO_ROOT, default_locations

RESERVE_BYTES = 256 * 1024 * 1024

@dataclass(frozen=True)
class Volume:
    id: str
    free_bytes: int


def volume_for(path: Path) -> Volume:
    if not path.is_absolute():
        raise ValueError('Choose absolute cache, app and model paths.')
    parent = path.resolve()
    while not parent.exists():
        if parent == parent.parent:
            raise ValueError('No existing volume for destination.')
        parent = parent.parent
    if not parent.is_dir():
        raise ValueError('Destination or an existing ancestor is a file.')
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        get_path = kernel.GetVolumePathNameW
        get_path.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
        get_path.restype = ctypes.c_int
        mount = ctypes.create_unicode_buffer(32768)
        if not get_path(str(parent), mount, len(mount)):
            raise ctypes.WinError(ctypes.get_last_error())
        get_name = kernel.GetVolumeNameForVolumeMountPointW
        get_name.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
        get_name.restype = ctypes.c_int
        name = ctypes.create_unicode_buffer(128)
        # Network shares may have no volume GUID; use their normalized mount path.
        identity = name.value if get_name(mount.value, name, len(name)) else mount.value
        identity = identity.casefold()
    else:
        identity = 'device:' + str(parent.stat().st_dev)
    return Volume(identity, shutil.disk_usage(parent).free)


def _verified_local(base: Path, name: object, digest: object) -> Path:
    import re
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
        raise ValueError('Resource references must be plain relative filenames.')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('Resource references require SHA-256.')
    path = base / name
    # Git may check out these text metadata files with CRLF on Windows.
    if hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest() != digest:
        raise ValueError('Resource reference changed: ' + name)
    return path


def load_resources(path: Path, repo_root: Path = REPO_ROOT):
    data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_keys)
    fields = {'schema_version','scope','python_version','platform','download_manifest',
              'download_manifest_sha256','lock_file','lock_sha256','source_sha256','layout','pending_components'}
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError('Invalid release resource metadata fields.')
    if type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise ValueError('Unsupported resource schema.')
    if (data['scope'] != 'python-runtime-dependencies-only' or data['python_version'] != '3.12'
            or data['platform'] != 'windows-x64'):
        raise ValueError('This resource subset targets CPython 3.12 / Windows x64 only.')
    source = data['source_sha256']
    if not isinstance(source, dict) or set(source) != {'pyproject.toml'}:
        raise ValueError('Expected the pyproject.toml source fingerprint.')
    _verified_local(repo_root, 'pyproject.toml', source['pyproject.toml'])
    manifest_path = _verified_local(path.parent, data['download_manifest'], data['download_manifest_sha256'])
    _verified_local(path.parent, data['lock_file'], data['lock_sha256'])
    manifest = load_manifest(manifest_path)
    layout = data['layout']
    if not isinstance(layout, dict) or set(layout) != {a.id for a in manifest.artifacts}:
        raise ValueError('Layout must cover every download artifact exactly.')
    for entry in layout.values():
        if not isinstance(entry, dict) or set(entry) != {'destination','unpacked_bytes'}:
            raise ValueError('Invalid artifact layout.')
        if not isinstance(entry['destination'], str) or entry['destination'] not in {'app','models'}:
            raise ValueError('Unsupported resource destination.')
        if type(entry['unpacked_bytes']) is not int or entry['unpacked_bytes'] < 0:
            raise ValueError('Unpacked sizes must be nonnegative integers.')
    pending = data['pending_components']
    required = {'python-runtime','node-runtime','tool-server','ollama-runtime','local-model','application'}
    if not isinstance(pending, list) or any(not isinstance(v, str) for v in pending) or set(pending) != required or len(pending) != len(required):
        raise ValueError('This dependency-only subset must declare its missing application components.')
    return manifest, data


def budget(manifest, layout: dict, *, cache_dir: Path, app_dir: Path, model_dir: Path,
           rollback_bytes: int = 0, cache_copies: int = 1, probe: Callable[[Path], Volume] = volume_for) -> list[dict]:
    if type(rollback_bytes) is not int or rollback_bytes < 0:
        raise ValueError('Rollback bytes must be nonnegative.')
    if type(cache_copies) is not int or cache_copies not in {1,2}:
        raise ValueError('Cache copies must be 1 or 2.')
    paths = {'cache': cache_dir, 'app': app_dir, 'models': model_dir}
    contributions = {'cache': manifest.download_bytes * cache_copies, 'app': rollback_bytes, 'models': 0}
    # Keep the full download cache and allow simultaneous staged and installed files.
    # The margin also covers wheel script/metadata generation and bytecode overhead.
    for artifact in manifest.artifacts:
        entry = layout[artifact.id]
        contributions[entry['destination']] += entry['unpacked_bytes'] * 2
    groups = {}
    for kind, amount in contributions.items():
        if not amount:
            continue
        volume = probe(paths[kind])
        group = groups.setdefault(volume.id, {'volume': volume.id, 'free_bytes': volume.free_bytes,
                                              'payload_bytes': 0, 'contributions': {}})
        group['free_bytes'] = min(group['free_bytes'], volume.free_bytes)
        group['payload_bytes'] += amount
        group['contributions'][kind] = amount
    result = []
    for group in groups.values():
        group['reserve_bytes'] = max(RESERVE_BYTES, (group['payload_bytes'] + 9) // 10)
        group['required_bytes'] = group['payload_bytes'] + group['reserve_bytes']
        group['sufficient_for_subset'] = group['free_bytes'] >= group['required_bytes']
        result.append(group)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    app, models = default_locations(os.environ, Path.home())
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--install-dir', type=Path, default=app)
    parser.add_argument('--model-dir', type=Path, default=models)
    parser.add_argument('--cache-dir', type=Path, default=app.parent.parent / 'Odessy' / 'downloads')
    parser.add_argument('--rollback-bytes', type=int, default=0)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        manifest, resources = load_resources(args.resources)
        volumes = budget(manifest, resources['layout'], cache_dir=args.cache_dir, app_dir=args.install_dir,
                         model_dir=args.model_dir, rollback_bytes=args.rollback_bytes)
        report = {'scope': resources['scope'], 'installation_ready': False,
                  'download_bytes': manifest.download_bytes, 'volumes': volumes,
                  'pending_components': resources['pending_components']}
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f'Python dependency subset: {len(manifest.artifacts)} wheels; {manifest.download_bytes} download bytes')
            for group in volumes:
                status = 'OK' if group['sufficient_for_subset'] else 'FAIL'
                print(f"[{status}] {group['volume']}: required {group['required_bytes']/1024**2:.1f} MiB, "
                      f"available {group['free_bytes']/1024**2:.1f} MiB; {group['contributions']}")
            print('PARTIAL BUDGET ONLY. Still missing: ' + ', '.join(resources['pending_components']))
            print('No directories created, downloads performed or software installed.')
        return 0 if all(v['sufficient_for_subset'] for v in volumes) else 1
    except (OSError, ValueError, ManifestError) as error:
        print('Budget failed:', error, file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
