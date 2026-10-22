"""Pinned model-store tests with tiny files; no external network or model inference."""
from __future__ import annotations
import base64
import contextlib
import hashlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from installer import model
from installer.download import DownloadError, IntegrityError


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / 'model store'; self.cache = self.root / 'cache'
        self.name = 'fixture:1b'
        payloads = [b'{"model_format":"gguf"}', b'fixture-model-weights', b'fixture-license']
        media = ['application/vnd.docker.container.image.v1+json', 'application/vnd.ollama.image.model', 'application/vnd.ollama.image.license']
        descriptors, rows = [], []
        self.sources = {}
        for index, (payload, media_type) in enumerate(zip(payloads, media)):
            checksum = hashlib.sha256(payload).hexdigest()
            source = self.root / f'blob-{index}'; source.write_bytes(payload); self.sources[f'model-blob-{index}'] = source
            descriptors.append({'mediaType': media_type, 'digest': 'sha256:' + checksum, 'size': len(payload)})
            rows.append({'id': f'model-blob-{index}', 'version': '1b',
                         'url': f'https://registry.ollama.ai/v2/library/fixture/blobs/sha256:{checksum}',
                         'size_bytes': len(payload), 'sha256': checksum})
        self.raw = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.docker.distribution.manifest.v2+json',
                              'config': descriptors[0], 'layers': descriptors[1:]}).encode()
        manifest = {'schema_version': 1, 'release': 'fixture-model', 'platform': 'windows-x64', 'artifacts': rows}
        content = json.dumps(manifest).encode(); (self.root / 'downloads.json').write_bytes(content)
        self.resources = self.root / 'resources.json'
        self.metadata = {'schema_version': 1, 'model': self.name,
            'registry_manifest_base64': base64.b64encode(self.raw).decode(),
            'registry_manifest_sha256': hashlib.sha256(self.raw).hexdigest(),
            'download_manifest': 'downloads.json', 'download_manifest_sha256': hashlib.sha256(content).hexdigest(),
            'source_url': 'https://registry.ollama.ai/v2/library/fixture/manifests/1b'}
        self.resources.write_text(json.dumps(self.metadata), encoding='utf-8')
        self.downloads = []
        self.print_patch = patch('builtins.print'); self.print_patch.start(); self.addCleanup(self.print_patch.stop)

    def fetch(self, artifact, cache):
        self.downloads.append(artifact.id)
        return SimpleNamespace(path=self.sources[artifact.id])

    def prepare(self, **kwargs):
        return model.prepare_model(self.store, self.cache, resources_path=self.resources,
                                   download=kwargs.pop('download', self.fetch), **kwargs)

    def verify(self):
        return model.verify_model(self.store, resources_path=self.resources)

    def test_prepare_native_store_and_offline_verify(self):
        report = self.prepare()
        self.assertEqual(model.manifest_path(self.store, self.name).read_bytes(), self.raw)
        manifest, _, _ = model.load_release(self.resources)
        for artifact in manifest.artifacts:
            self.assertEqual(model.blob_path(self.store, artifact).read_bytes(), self.sources[artifact.id].read_bytes())
        self.assertEqual(report['files_verified'], 3)
        self.assertFalse(report['inference_verified']); self.assertFalse(report['installation_ready'])
        self.assertEqual(self.verify(), report)

    def test_repeated_prepare_reuses_verified_files_without_network(self):
        self.prepare(); self.downloads.clear()
        self.prepare()
        self.assertEqual(self.downloads, [])

    def test_wrong_existing_tag_is_preserved_before_download(self):
        target = model.manifest_path(self.store, self.name); target.parent.mkdir(parents=True); target.write_bytes(b'other-model')
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(target.read_bytes(), b'other-model'); self.assertEqual(self.downloads, [])

    def test_corrupt_existing_blob_is_preserved(self):
        self.prepare()
        manifest, _, _ = model.load_release(self.resources)
        blob = model.blob_path(self.store, manifest.artifacts[1]); blob.write_bytes(b'corrupt')
        with self.assertRaises(IntegrityError): self.prepare()
        self.assertEqual(blob.read_bytes(), b'corrupt')
        with self.assertRaises(IntegrityError): self.verify()

    def test_wrong_download_hash_does_not_publish_tag(self):
        self.sources['model-blob-1'].write_bytes(b'wrong-model')
        with self.assertRaises(IntegrityError): self.prepare()
        self.assertFalse(model.manifest_path(self.store, self.name).exists())
        self.assertFalse(list(self.store.rglob('.odessy-*')))

    def test_partial_download_failure_keeps_completed_blobs_for_retry(self):
        def fail(artifact, cache):
            if artifact.id == 'model-blob-1': raise OSError('fixture failure')
            return self.fetch(artifact, cache)
        with self.assertRaises(OSError): self.prepare(download=fail)
        self.assertFalse(model.manifest_path(self.store, self.name).exists())
        self.downloads.clear(); self.prepare()
        self.assertEqual(self.downloads, ['model-blob-1', 'model-blob-2'])

    def test_cancel_before_publication_keeps_no_incomplete_copy(self):
        with patch.object(model, 'publish_new', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.prepare()
        self.assertFalse(model.manifest_path(self.store, self.name).exists())
        self.assertFalse(list(self.store.rglob('.odessy-*')))

    def test_space_failure_performs_no_download_or_write(self):
        with patch.object(model, 'model_budget', return_value=[{'sufficient_for_subset': False}]), self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.downloads, []); self.assertFalse(self.store.exists())

    def test_invalid_resource_digest_and_missing_layer_rejected(self):
        original = dict(self.metadata)
        for key, value in (('registry_manifest_sha256', '0' * 64), ('source_url', 'https://example.com/manifest'),
                           ('model', '../bad:1b'), ('registry_manifest_base64', 'bad***')):
            with self.subTest(key=key):
                data = dict(original); data[key] = value; self.resources.write_text(json.dumps(data))
                with self.assertRaises(ValueError): model.load_release(self.resources)
        self.resources.write_text(json.dumps(original))
        document = json.loads((self.root / 'downloads.json').read_text()); document['artifacts'].pop()
        content = json.dumps(document).encode(); (self.root / 'downloads.json').write_bytes(content)
        self.metadata['download_manifest_sha256'] = hashlib.sha256(content).hexdigest()
        self.resources.write_text(json.dumps(self.metadata))
        with self.assertRaises(ValueError): model.load_release(self.resources)

    def test_concurrent_conflicting_manifest_is_not_overwritten(self):
        original = model.publish_new
        def publish(source, target):
            if target.name == '1b':
                target.write_bytes(b'concurrent owner')
            original(source, target)
        with patch.object(model, 'publish_new', side_effect=publish), self.assertRaises(ValueError): self.prepare()
        self.assertEqual(model.manifest_path(self.store, self.name).read_bytes(), b'concurrent owner')
        self.assertFalse(list(self.store.rglob('.odessy-*')))

    def test_manifest_not_published_until_license_is_verified(self):
        def fetch(artifact, cache):
            self.assertFalse(model.manifest_path(self.store, self.name).exists())
            if artifact.id == 'model-blob-2': raise OSError('license failure')
            return self.fetch(artifact, cache)
        with self.assertRaises(OSError): self.prepare(download=fetch)
        self.assertFalse(model.manifest_path(self.store, self.name).exists())

    def test_service_digest_check_never_accepts_name_alone(self):
        report = self.prepare()
        for digest, expected in ((report['manifest_sha256'], True), ('sha256:' + report['manifest_sha256'], True),
                                  ('f' * 64, False), (None, False)):
            fetch = lambda *a: {'models': [{'name': self.name, 'digest': digest}]}
            if expected:
                model.verify_service('http://localhost:11434', report, fetch=fetch)
            else:
                with self.assertRaises(ValueError): model.verify_service('http://localhost:11434', report, fetch=fetch)
        with self.assertRaises(ValueError):
            model.verify_service('http://localhost:11434', report, fetch=lambda *a: {'models': []})

    def test_relative_or_drive_root_store_rejected(self):
        for path in (Path('relative'), Path(self.root.anchor), Path.home()):
            with self.assertRaises(ValueError): model.guard_path(path)

    def test_preview_is_read_only_and_does_not_check_service(self):
        release = model.load_release(self.resources)
        with patch.object(model, 'load_release', return_value=release), \
             patch.object(model, 'repository_defaults', return_value={'ODESSY_MODEL_NAME': self.name}), \
             patch.object(model, 'prepare_model') as prepare, patch.object(model, 'verify_service') as service:
            self.assertEqual(model.main(['--model-dir', str(self.store), '--cache-dir', str(self.cache),
                                         '--endpoint', 'http://127.0.0.1:11434']), 0)
        prepare.assert_not_called(); service.assert_not_called()
        self.assertFalse(self.store.exists()); self.assertFalse(self.cache.exists())

    def test_reparse_store_is_rejected(self):
        original = Path.lstat
        def info(path):
            if path == self.store:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path)
        with patch.object(Path, 'lstat', info), self.assertRaises(DownloadError):
            self.prepare()
        self.assertEqual(self.downloads, [])
        self.assertFalse(self.store.exists())


if __name__ == '__main__':
    unittest.main()
