"""Developer-only: verify and pin the opt-in voice artifacts.

Writes `src/odessy/setup/voice-artifacts.json` from files already present in a
cache directory, so every hash in the shipped manifest is reproducible:

    python installer/build/pin_voice_manifest.py --cache .voice-cache

Each artifact is hashed twice independently -- once from the file on disk and
once from the publisher's own metadata (PyPI JSON digests for wheels, the
GitHub release asset digest for the release downloads) -- and the script fails
if they disagree. The HuggingFace whisper model publishes no digest, so its
hash is only the file's own; the URL is pinned to a repository revision instead
of a moving branch, which is what makes that hash stable.

Nothing here runs during an installation. It is a release step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Release downloads. `file` is the name in the cache; `target` is what the
#: installer writes into the voice directory.
RELEASES = [
    {
        'id': 'whisper-cli',
        'label': 'Whisper.cpp executable',
        'kind': 'archive',
        'cache': 'whisper-bin-x64.zip',
        'members': ['whisper-cli.exe', 'whisper.dll', 'ggml.dll', 'ggml-base.dll', 'ggml-cpu.dll'],
        'strip_prefix': 'Release/',
        'executable': 'whisper-cli.exe',
        'url': 'https://github.com/ggml-org/whisper.cpp/releases/download/v1.8.2/whisper-bin-x64.zip',
        'license': 'MIT',
        'provenance': 'github-release-digest',
    },
    {
        'id': 'whisper-model',
        'label': 'Whisper model (ggml-base.en)',
        'kind': 'file',
        'cache': 'ggml-base.en.bin',
        'field': 'whisper_model',
        'target': 'ggml-base.en.bin',
        'url': ('https://huggingface.co/ggerganov/whisper.cpp/resolve/'
                '5359861c739e955e79d9a303bcbc70fb988958b1/ggml-base.en.bin'),
        'license': 'MIT',
        'provenance': 'file-only',
    },
    {
        'id': 'kokoro-model',
        'label': 'Kokoro model (int8)',
        'kind': 'file',
        'cache': 'kokoro-v1.0.int8.onnx',
        'field': 'kokoro_model',
        'target': 'kokoro-v1.0.int8.onnx',
        'url': ('https://github.com/thewh1teagle/kokoro-onnx/releases/download/'
                'model-files-v1.1/kokoro-v1.0.int8.onnx'),
        'license': 'Apache-2.0',
        'provenance': 'github-release-digest',
    },
    {
        'id': 'kokoro-voices',
        'label': 'Kokoro voices',
        'kind': 'file',
        'cache': 'voices-v1.0.bin',
        'field': 'kokoro_voices',
        'target': 'voices-v1.0.bin',
        'url': ('https://github.com/thewh1teagle/kokoro-onnx/releases/download/'
                'model-files-v1.1/voices-v1.0.bin'),
        'license': 'Apache-2.0',
        'provenance': 'github-release-digest',
    },
]

#: The Python side of the voice stack, resolved for CPython 3.12 / win_amd64.
#: `pip download --only-binary=:all: --platform win_amd64 --python-version 312
#: --implementation cp --abi cp312` produced this set; the licensing column
#: records what ships inside each wheel.
WHEEL_LICENSES = {
    'numpy': 'BSD-3-Clause',
    'onnxruntime': 'MIT',
    'sounddevice': 'MIT',
    'soundfile': 'BSD-3-Clause',
    'cffi': 'MIT',
    'pycparser': 'BSD-3-Clause',
    'kokoro-onnx': 'Apache-2.0',
    'phonemizer': 'GPL-3.0-or-later',
    'espeakng-loader': 'GPL-3.0-or-later',
    'dlinfo': 'MIT',
    'joblib': 'BSD-3-Clause',
    'cloudpickle': 'BSD-3-Clause',
    'packaging': 'Apache-2.0 OR BSD-2-Clause',
    'attrs': 'MIT',
    'protobuf': 'BSD-3-Clause',
    'flatbuffers': 'Apache-2.0',
    'typing-extensions': 'PSF-2.0',
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def github_digests() -> dict:
    """Release asset digests, keyed by download URL."""
    digests = {}
    for repo in ('ggml-org/whisper.cpp', 'thewh1teagle/kokoro-onnx'):
        url = f'https://api.github.com/repos/{repo}/releases?per_page=30'
        request = urllib.request.Request(url, headers={'User-Agent': 'odessy-pin'})
        with urllib.request.urlopen(request, timeout=30) as response:
            for release in json.load(response):
                for asset in release.get('assets', []):
                    digest = asset.get('digest') or ''
                    if digest.startswith('sha256:'):
                        digests[asset['browser_download_url']] = digest[7:]
    return digests


def pypi_row(name: str, version: str, filename: str, size: int, digest: str) -> dict:
    url = f'https://pypi.org/pypi/{name}/{version}/json'
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            meta = json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(f'{filename}: PyPI has no {name} {version} ({error.code})') from None
    candidate = next((item for item in meta['urls'] if item['filename'] == filename), None)
    if candidate is None:
        raise SystemExit(f'{filename} is not published by {name} {version}')
    if candidate['digests']['sha256'] != digest or candidate['size'] != size:
        raise SystemExit(f'{filename} does not match the digest PyPI publishes')
    return candidate


def wheel_entries(directory: Path) -> list:
    entries = []
    for path in sorted(directory.glob('*.whl')):
        # {name}-{version}(-{build})?-{python}-{abi}-{platform}.whl: the last three
        # fields are fixed, so the build tag cannot be mistaken for a version.
        base = path.name[:-4].split('-')
        name, version = base[0], base[1]
        digest = sha256(path)
        candidate = pypi_row(name, version, path.name, path.stat().st_size, digest)
        entries.append({
            'id': f'py-{name}',
            'label': f'{name} {version}',
            'kind': 'wheel',
            'file': path.name,
            'url': candidate['url'],
            'sha256': digest,
            'size_bytes': path.stat().st_size,
            'license': WHEEL_LICENSES.get(name, ''),
            'platform': 'windows',
            'architecture': 'x86_64',
            'provenance': 'pypi-digest',
        })
    return entries


def build(cache: Path, wheels: Path) -> dict:
    digests = github_digests()
    artifacts = []
    for spec in RELEASES:
        path = cache / spec['cache']
        if not path.is_file():
            raise SystemExit(f'missing {path}; download it first from {spec["url"]}')
        digest = sha256(path)
        if spec['provenance'] == 'github-release-digest':
            published = digests.get(spec['url'])
            if published is None:
                raise SystemExit(f'no published digest for {spec["url"]}')
            if published != digest:
                raise SystemExit(f'{spec["cache"]} does not match the published digest')
        entry = {
            'id': spec['id'], 'label': spec['label'], 'kind': spec['kind'],
            'file': spec['target'] if spec['kind'] == 'file' else spec['cache'],
            'url': spec['url'], 'sha256': digest, 'size_bytes': path.stat().st_size,
            'license': spec['license'], 'platform': 'windows', 'architecture': 'x86_64',
            'provenance': spec['provenance'],
        }
        if spec['kind'] == 'file':
            entry['field'] = spec['field']
        else:
            entry['members'] = spec['members']
            entry['strip_prefix'] = spec['strip_prefix']
            entry['executable'] = spec['executable']
            entry['field'] = 'whisper_bin'
        artifacts.append(entry)
    artifacts.extend(wheel_entries(wheels))
    return {'schema_version': 2, 'artifacts': artifacts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=REPO_ROOT / '.voice-cache')
    parser.add_argument('--wheels', type=Path, default=None)
    parser.add_argument('--output', type=Path,
                        default=REPO_ROOT / 'src' / 'odessy' / 'setup' / 'voice-artifacts.json')
    args = parser.parse_args()
    wheels = args.wheels or (args.cache / 'wheels')
    manifest = build(args.cache, wheels)
    text = json.dumps(manifest, indent=2) + '\n'
    args.output.write_text(text, encoding='utf-8', newline='\n')
    total = sum(a['size_bytes'] for a in manifest['artifacts'])
    print(f'pinned {len(manifest["artifacts"])} artifacts, {total} bytes -> {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
