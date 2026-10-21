"""Prepare the content-pinned default model in a native private Ollama store."""
from __future__ import annotations
import argparse
import base64
import binascii
import hashlib
import http.client
import json
import os
import re
import tempfile
from pathlib import Path

from installer.download import DownloadError, IntegrityError, _reject_link, download_artifact, verified
from installer.manifest import _unique_keys, load_manifest
from installer.ollama import get_json, local_endpoint
from installer.preflight import REPO_ROOT, default_locations, repository_defaults
from installer.space import _verified_local, budget

DEFAULT_RESOURCES = Path(__file__).resolve().parent / 'releases/lfm2.5-thinking-1.2b-q8_0/model.resources.json'


def load_release(path: Path = DEFAULT_RESOURCES):
    data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_keys)
    fields = {'schema_version', 'model', 'registry_manifest_base64', 'registry_manifest_sha256',
              'download_manifest', 'download_manifest_sha256', 'source_url'}
    if (not isinstance(data, dict) or set(data) != fields or type(data['schema_version']) is not int
        or data['schema_version'] != 1 or not isinstance(data['model'], str)
        or not re.fullmatch(r'[a-z0-9][a-z0-9._-]*:[a-z0-9][a-z0-9._-]*', data['model'])):
        raise ValueError('Invalid pinned model resource metadata.')
    name, tag = data['model'].split(':')
    if data['source_url'] != f'https://registry.ollama.ai/v2/library/{name}/manifests/{tag}':
        raise ValueError('Expected the official registry model manifest source.')
    try:
        raw = base64.b64decode(data['registry_manifest_base64'], validate=True)
    except (ValueError, TypeError, binascii.Error) as error:
        raise ValueError('Invalid model manifest encoding.') from error
    if not raw or len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != data['registry_manifest_sha256']:
        raise ValueError('Pinned registry manifest digest mismatch.')
    document = json.loads(raw, object_pairs_hook=_unique_keys)
    if (not isinstance(document, dict) or set(document) != {'schemaVersion', 'mediaType', 'config', 'layers'}
        or type(document['schemaVersion']) is not int or document['schemaVersion'] != 2
        or document['mediaType'] != 'application/vnd.docker.distribution.manifest.v2+json'
        or not isinstance(document['layers'], list) or not document['layers'] or len(document['layers']) > 50):
        raise ValueError('Unsupported registry manifest schema.')
    manifest = load_manifest(_verified_local(path.parent, data['download_manifest'], data['download_manifest_sha256']))
    descriptors = [document['config'], *document['layers']]
    if len(manifest.artifacts) != len(descriptors):
        raise ValueError('Model downloads must cover the config and every layer exactly.')
    allowed = {'application/vnd.ollama.image.model', 'application/vnd.ollama.image.license',
               'application/vnd.ollama.image.params', 'application/vnd.ollama.image.template',
               'application/vnd.ollama.image.system'}
    digests = set()
    for index, (descriptor, artifact) in enumerate(zip(descriptors, manifest.artifacts)):
        if not isinstance(descriptor, dict) or set(descriptor) != {'mediaType', 'digest', 'size'}:
            raise ValueError('Unsupported model layer descriptor.')
        expected_type = {'application/vnd.docker.container.image.v1+json'} if index == 0 else allowed
        if not isinstance(descriptor['mediaType'], str) or descriptor['mediaType'] not in expected_type:
            raise ValueError('Unsupported model layer media type.')
        if (descriptor['digest'] != 'sha256:' + artifact.sha256 or type(descriptor['size']) is not int
            or descriptor['size'] != artifact.size_bytes or artifact.version != tag
            or artifact.url != f'https://registry.ollama.ai/v2/library/{name}/blobs/sha256:{artifact.sha256}'
            or artifact.sha256 in digests):
            raise ValueError('Model layer digest, size, version or official URL differs from the pinned manifest.')
        digests.add(artifact.sha256)
    if not any(layer['mediaType'] == 'application/vnd.ollama.image.model' for layer in document['layers']):
        raise ValueError('Pinned model weights are missing.')
    return manifest, data, raw


def guard_path(path: Path):
    if not path.is_absolute() or path == Path(path.anchor) or path == Path.home():
        raise ValueError('Choose dedicated absolute model and cache paths, outside the drive root or home root.')
    # Do not follow an existing junction/symlink in any store path component.
    for part in [path, *path.parents]:
        _reject_link(part)


def manifest_path(root: Path, model: str) -> Path:
    name, tag = model.split(':')
    return root / 'manifests' / 'registry.ollama.ai' / 'library' / name / tag


def blob_path(root: Path, artifact) -> Path:
    return root / 'blobs' / ('sha256-' + artifact.sha256)


def check_existing(root: Path, manifest, resources, raw: bytes):
    guard_path(root)
    path = manifest_path(root, resources['model']); guard_path(path)
    if path.exists() and (not path.is_file() or path.read_bytes() != raw):
        raise ValueError('A different model manifest already uses this tag; existing model is preserved. Choose a new model directory.')
    for artifact in manifest.artifacts:
        target = blob_path(root, artifact); guard_path(target)
        if target.exists() and not verified(target, artifact):
            raise IntegrityError('An existing model blob is corrupt; it was preserved. Choose a new model directory.')


def model_budget(manifest, root: Path, cache: Path):
    guard_path(root); guard_path(cache)
    return budget(manifest, {a.id: {'destination': 'models', 'unpacked_bytes': a.size_bytes} for a in manifest.artifacts},
                  cache_dir=cache, app_dir=root, model_dir=root)


def publish_new(source: Path, target: Path):
    """Atomic publication without replacement. Both paths are on the same volume."""
    if os.name == 'nt':
        # Windows rename rejects an existing target; POSIX rename would overwrite it.
        source.rename(target)
    else:
        os.link(source, target)
        source.unlink()


def install_blob(source: Path, target: Path, artifact):
    guard_path(target)
    if target.exists():
        if verified(target, artifact):
            return
        raise IntegrityError('Existing model blob is invalid; it was preserved.')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.odessy-model-', suffix='.part', dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            with source.open('rb') as incoming:
                received = 0
                while chunk := incoming.read(min(1024 * 1024, artifact.size_bytes - received + 1)):
                    received += len(chunk)
                    if received > artifact.size_bytes:
                        raise IntegrityError('Model copy exceeds the pinned size.')
                    stream.write(chunk)
                if received != artifact.size_bytes:
                    raise IntegrityError('Model copy is truncated.')
            stream.flush(); os.fsync(stream.fileno())
        if not verified(temporary, artifact):
            raise IntegrityError('Model blob changed while copying; model was not published.')
        try:
            publish_new(temporary, target)
        except FileExistsError:
            if not verified(target, artifact):
                raise IntegrityError('A conflicting blob appeared; it was preserved.')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_model(root: Path, cache: Path, *, resources_path=DEFAULT_RESOURCES, download=download_artifact):
    manifest, resources, raw = load_release(resources_path)
    check_existing(root, manifest, resources, raw)
    if not all(v['sufficient_for_subset'] for v in model_budget(manifest, root, cache)):
        raise ValueError('Not enough space for the model cache, installed files, temporary copy and reserve.')
    for artifact in manifest.artifacts:
        target = blob_path(root, artifact)
        if verified(target, artifact):
            print('[REUSE] verified installed blob:', artifact.sha256[:12], flush=True)
            continue
        print('Preparing model layer:', artifact.id, flush=True)
        downloaded = download(artifact, cache).path
        install_blob(downloaded, target, artifact)
    # Publish the tag only after every layer, including its license, is verified.
    check_existing(root, manifest, resources, raw)
    for artifact in manifest.artifacts:
        if not verified(blob_path(root, artifact), artifact):
            raise IntegrityError('A model blob is missing or changed before manifest publication.')
    target = manifest_path(root, resources['model'])
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix='.odessy-manifest-', dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name); stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            try:
                publish_new(temporary, target)
            except FileExistsError:
                if target.read_bytes() != raw:
                    raise ValueError('A different manifest appeared; it was preserved.')
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return verify_model(root, resources_path=resources_path)


def verify_model(root: Path, *, resources_path=DEFAULT_RESOURCES) -> dict:
    manifest, resources, raw = load_release(resources_path)
    check_existing(root, manifest, resources, raw)
    if not manifest_path(root, resources['model']).is_file():
        raise ValueError('The pinned model manifest has not been installed.')
    for artifact in manifest.artifacts:
        if not verified(blob_path(root, artifact), artifact):
            raise IntegrityError('A pinned model file is missing or corrupt: ' + artifact.id)
    return {'model': resources['model'], 'manifest_sha256': resources['registry_manifest_sha256'],
            'files_verified': len(manifest.artifacts), 'model_bytes': manifest.download_bytes,
            'model_dir': str(root), 'inference_verified': False, 'installation_ready': False}


def verify_service(endpoint: str, report: dict, *, fetch=get_json):
    data = fetch(local_endpoint(endpoint), '/api/tags', 3.0)
    entries = data.get('models')
    if not isinstance(entries, list):
        raise ValueError('Service model list is invalid.')
    for entry in entries:
        if isinstance(entry, dict) and entry.get('name') == report['model']:
            digest = entry.get('digest')
            if not isinstance(digest, str) or digest.removeprefix('sha256:') != report['manifest_sha256']:
                raise ValueError('Service has a different digest for this model tag; its files are unchanged.')
            return
    raise ValueError('Pinned model is not listed by this endpoint. Check the running service model directory.')


def main(argv=None):
    app, models = default_locations(os.environ, Path.home())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, default=models)
    parser.add_argument('--cache-dir', type=Path, default=app.parent.parent / 'Odessy/downloads')
    parser.add_argument('--endpoint', help='Optionally check the running service lists this exact manifest digest after preparation/verification.')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--prepare', action='store_true')
    action.add_argument('--verify', action='store_true')
    args = parser.parse_args(argv)
    try:
        manifest, resources, raw = load_release()
        defaults = repository_defaults(REPO_ROOT / 'config/odessy.env')
        if resources['model'] != defaults['ODESSY_MODEL_NAME']:
            raise ValueError('Repository model default changed; a reviewed matching model release is required.')
        if args.endpoint:
            local_endpoint(args.endpoint)
        check_existing(args.model_dir, manifest, resources, raw)
        if not args.verify:
            print('Pinned model:', resources['model'])
            print('Manifest SHA-256:', resources['registry_manifest_sha256'])
            print(f'Model files: {len(manifest.artifacts)}; {manifest.download_bytes / 1024**3:.2f} GiB; {manifest.download_bytes} bytes')
            print('Native Ollama model directory:', args.model_dir)
            volumes = model_budget(manifest, args.model_dir, args.cache_dir)
            for volume in volumes:
                status = 'OK' if volume['sufficient_for_subset'] else 'FAIL'
                print(f'[{status}] required {volume["required_bytes"] / 1024**3:.2f} GiB; available {volume["free_bytes"] / 1024**3:.2f} GiB')
            if not all(v['sufficient_for_subset'] for v in volumes):
                return 1
        if not args.prepare and not args.verify:
            print('PREVIEW ONLY. --prepare downloads/verifies model files; --verify checks installed files.')
            print('No downloads, writes, service changes or inference performed.')
            return 0
        last = {}
        def fetch(artifact, cache):
            def progress(name, received, total):
                percent = received * 100 // total
                if percent // 5 != last.get(name, -1) // 5 or received == total:
                    print(f'[DOWNLOAD] {name}: {percent}% ({received}/{total} bytes)', flush=True)
                    last[name] = percent
            return download_artifact(artifact, cache, progress=progress)
        report = (prepare_model(args.model_dir, args.cache_dir, download=fetch) if args.prepare
                  else verify_model(args.model_dir))
        print('Model files verified:', report['files_verified'], 'SHA-256 checks passed.')
        if args.endpoint:
            verify_service(args.endpoint, report)
            print('Service lists the pinned model digest:', local_endpoint(args.endpoint))
        print('No configuration changes, service restarts or model inference performed.')
        print('Real inference, hardware fitness and complete installation remain unverified.')
        return 0
    except KeyboardInterrupt:
        print('Cancelled. Incomplete installer copies cleaned; verified blobs/cache retained. Rerun to reuse them.')
        return 130
    except (OSError, ValueError, DownloadError, http.client.HTTPException) as error:
        print('Model preparation failed:', error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
