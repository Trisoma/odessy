"""Developer-only: turn resolved Windows wheels into a pinned release subset."""
from __future__ import annotations
import argparse
import concurrent.futures
import email
import hashlib
import json
import re
import urllib.request
import zipfile
from pathlib import Path
from installer.manifest import Artifact, ManifestError, load_manifest
from installer.dependency_spec import fingerprint as dependency_fingerprint

REPO_ROOT = Path(__file__).resolve().parents[1]

def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()

def resolved_packages(path: Path) -> dict:
    packages = {}
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)\s+((?:--hash=sha256:[0-9a-f]{64}\s*)+)", line)
        if not match:
            raise ValueError("Expected exact hash-locked, marker-free requirements.")
        name, version, hashes = match.groups()
        key = canonical(name)
        if key in packages:
            raise ValueError("Duplicate locked package: " + key)
        packages[key] = (version, set(re.findall(r"sha256:([0-9a-f]{64})", hashes)))
    if not packages:
        raise ValueError("The resolved lock is empty.")
    return packages

def inspect_wheel(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        entries = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
        if len(entries) != 1:
            raise ValueError("Wheel must contain exactly one METADATA file.")
        metadata = email.message_from_bytes(archive.read(entries[0]))
        size = sum(i.file_size for i in archive.infolist())
    if not metadata['Name'] or not metadata['Version']:
        raise ValueError("Missing wheel name or version.")
    return {"id": canonical(metadata['Name']), "version": metadata['Version'],
            "filename": path.name, "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "unpacked_bytes": size}

def supports_target(filename: str) -> bool:
    if not filename.endswith('.whl'):
        return False
    parts = filename[:-4].rsplit('-', 3)
    if len(parts) != 4:
        return False
    _, interpreters, abi, platforms = parts
    if not set(platforms.split('.')) & {'any','win_amd64'}:
        return False
    for interpreter in interpreters.split('.'):
        if abi == 'none' and (interpreter == 'py3' or
                (re.fullmatch(r'py3[0-9]+', interpreter) and int(interpreter[2:]) <= 312)):
            return True
        if 'win_amd64' in platforms.split('.'):
            if interpreter == 'cp312' and abi == 'cp312':
                return True
            if re.fullmatch(r'cp3[0-9]+', interpreter) and abi == 'abi3' and 32 <= int(interpreter[2:]) <= 312:
                return True
    return False

def pypi_url(row: dict) -> str:
    url = f"https://pypi.org/pypi/{row['id']}/{row['version']}/json"
    with urllib.request.urlopen(url, timeout=30) as response:
        candidates = json.load(response)['urls']
    candidate = next((c for c in candidates if c['filename'] == row['filename']), None)
    if (candidate is None or candidate['size'] != row['size_bytes']
            or candidate['digests']['sha256'] != row['sha256']):
        raise ValueError("PyPI metadata does not match wheel: " + row['id'])
    return candidate['url']

def prepare(wheel_dir: Path, resolved_lock: Path, output_dir: Path, *,
            repo_root: Path = REPO_ROOT, lookup=pypi_url) -> dict:
    packages = resolved_packages(resolved_lock)
    rows = [inspect_wheel(p) for p in sorted(wheel_dir.glob('*.whl'))]
    ids = [r['id'] for r in rows]
    if len(set(ids)) != len(ids) or set(ids) != set(packages):
        raise ValueError("Wheel set must match all locked packages exactly, without duplicates.")
    for row in rows:
        version, hashes = packages[row['id']]
        if row['version'] != version or row['sha256'] not in hashes:
            raise ValueError("Wheel does not match resolved lock: " + row['id'])
        if not supports_target(row['filename']):
            raise ValueError("Wheel must support CPython 3.12 on Windows x64.")
    artifacts = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        urls = list(pool.map(lookup, rows))
    for row, url in zip(rows, urls):
        artifact = Artifact(row['id'], row['version'], url, row['size_bytes'], row['sha256'])
        artifacts.append({k: getattr(artifact, k) for k in ('id','version','url','size_bytes','sha256')})
    manifest = {"schema_version": 1, "release": "python-deps-windows-x64-py312",
                "platform": "windows-x64", "artifacts": artifacts}
    manifest_text = json.dumps(manifest, indent=2) + '\n'
    lock_text = '# Windows x64 / CPython 3.12 runtime dependencies only.\n'
    lock_text += '# Derived from pyproject.toml; each hash selects one verified wheel.\n'
    lock_text += ''.join(f"{r['id']}=={r['version']} --hash=sha256:{r['sha256']}\n" for r in rows)
    manifest_name = 'python-wheels-windows-x64.json'
    lock_name = 'requirements-windows-x64-py312.txt'
    resources = {
        "schema_version": 2, "scope": "python-runtime-dependencies-only",
        "python_version": "3.12", "platform": "windows-x64",
        "download_manifest": manifest_name,
        "download_manifest_sha256": hashlib.sha256(manifest_text.encode()).hexdigest(),
        "lock_file": lock_name, "lock_sha256": hashlib.sha256(lock_text.encode()).hexdigest(),
        "dependency_spec_sha256": dependency_fingerprint(repo_root/'pyproject.toml'),
        "layout": {r['id']: {"destination": "app", "unpacked_bytes": r['unpacked_bytes']} for r in rows},
        "pending_components": ["python-runtime", "node-runtime", "tool-server", "ollama-runtime", "local-model", "application"],
    }
    outputs = {manifest_name: manifest_text, lock_name: lock_text,
               'python-wheels.resources.json': json.dumps(resources, indent=2) + '\n'}
    if any((output_dir/name).exists() for name in outputs):
        raise ValueError("Output files already exist; use a new release directory.")
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in outputs.items():
        (output_dir/name).write_text(content, encoding='utf-8', newline='\n')
    load_manifest(output_dir/manifest_name)
    return {"packages": len(rows), "download_bytes": sum(r['size_bytes'] for r in rows),
            "unpacked_bytes": sum(r['unpacked_bytes'] for r in rows)}

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel-dir', type=Path, required=True)
    parser.add_argument('--resolved-lock', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.wheel_dir, args.resolved_lock, args.output_dir), indent=2))
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print('Preparation failed:', error)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
