"""Measured dependency-resource preparation and disk-budget tests."""
from __future__ import annotations
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from installer.manifest import load_manifest
from installer.prepare_wheels import prepare, resolved_packages, supports_target
from installer.space import RESERVE_BYTES, Volume, budget, load_resources, volume_for

class SpaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'pyproject.toml').write_text('[project]\nname="fixture"\n', encoding='utf-8')
        self.wheels = self.root/'wheels'
        self.wheels.mkdir()
        wheel = self.wheels/'demo-1.0.0-py3-none-any.whl'
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('demo/__init__.py', 'value = 1\n')
            archive.writestr('demo-1.0.0.dist-info/METADATA', 'Name: demo\nVersion: 1.0.0\n')
        self.digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        self.lock = self.root/'resolved.txt'
        self.lock.write_text(f'demo==1.0.0 --hash=sha256:{self.digest}\n', encoding='utf-8')
        self.output = self.root/'release'

    def prepare(self):
        return prepare(self.wheels, self.lock, self.output, repo_root=self.root,
                       lookup=lambda row: 'https://files.pythonhosted.org/fixture/'+row['filename'])

    def load(self):
        return load_resources(self.output/'python-wheels.resources.json', self.root)

    def test_preparation_measures_real_archive_and_pins_one_hash(self):
        result = self.prepare()
        self.assertEqual(result['packages'], 1)
        manifest, resource = self.load()
        self.assertEqual(manifest.artifacts[0].sha256, self.digest)
        self.assertEqual(resource['layout']['demo']['unpacked_bytes'], result['unpacked_bytes'])
        self.assertIn('demo==1.0.0 --hash=sha256:'+self.digest,
                      (self.output/'requirements-windows-x64-py312.txt').read_text())

    def test_hash_mismatch_refuses_preparation_without_output(self):
        self.lock.write_text('demo==1.0.0 --hash=sha256:'+'0'*64+'\n')
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.output.exists())

    def test_missing_wheel_refuses_preparation(self):
        self.lock.write_text(self.lock.read_text()+'absent==1.0.0 --hash=sha256:'+'a'*64+'\n')
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.output.exists())

    def test_preparation_does_not_overwrite_existing_release(self):
        self.prepare()
        original = (self.output/'python-wheels.resources.json').read_bytes()
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual((self.output/'python-wheels.resources.json').read_bytes(), original)

    def test_changed_source_manifest_or_lock_is_detected(self):
        self.prepare()
        paths = [self.root/'pyproject.toml', self.output/'python-wheels-windows-x64.json',
                 self.output/'requirements-windows-x64-py312.txt']
        for path in paths:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original+b'\n')
                with self.assertRaises(ValueError): self.load()
                path.write_bytes(original)

    def test_git_crlf_checkout_preserves_metadata_fingerprints(self):
        self.prepare()
        for path in (self.root/'pyproject.toml', self.output/'python-wheels-windows-x64.json',
                     self.output/'requirements-windows-x64-py312.txt'):
            path.write_bytes(path.read_bytes().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n'))
        manifest, _ = self.load()
        self.assertEqual(len(manifest.artifacts), 1)

    def test_same_volume_adds_cache_and_install_budget_once(self):
        self.prepare()
        manifest, data = self.load()
        groups = budget(manifest, data['layout'], cache_dir=self.root/'cache', app_dir=self.root/'app',
                        model_dir=self.root/'models', rollback_bytes=100,
                        probe=lambda path: Volume('same', 10**10))
        self.assertEqual(len(groups), 1)
        expected = manifest.download_bytes + data['layout']['demo']['unpacked_bytes']*2 + 100
        self.assertEqual(groups[0]['payload_bytes'], expected)
        self.assertEqual(groups[0]['required_bytes'], expected+RESERVE_BYTES)
        self.assertTrue(groups[0]['sufficient_for_subset'])

    def test_separate_volumes_have_separate_budgets(self):
        self.prepare()
        manifest, data = self.load()
        groups = budget(manifest, data['layout'], cache_dir=self.root/'cache', app_dir=self.root/'app',
                        model_dir=self.root/'models', probe=lambda path: Volume(path.name, 10**10))
        self.assertEqual(len(groups), 2)
        self.assertEqual({g['volume'] for g in groups}, {'cache','app'})

    def test_low_space_is_a_blocker_and_no_directories_are_created(self):
        self.prepare()
        manifest, data = self.load()
        groups = budget(manifest, data['layout'], cache_dir=self.root/'cache', app_dir=self.root/'app',
                        model_dir=self.root/'models', probe=lambda path: Volume('same', 1))
        self.assertFalse(groups[0]['sufficient_for_subset'])
        self.assertFalse((self.root/'app').exists())
        self.assertFalse((self.root/'cache').exists())

    def test_metadata_cannot_hide_missing_application_components(self):
        self.prepare()
        path = self.output/'python-wheels.resources.json'
        data = json.loads(path.read_text())
        data['pending_components'] = []
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): self.load()

    def test_resource_paths_cannot_traverse_outside_release_directory(self):
        self.prepare()
        path = self.output/'python-wheels.resources.json'
        data = json.loads(path.read_text())
        data['lock_file'] = '../resolved.txt'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): self.load()

    def test_bad_layout_sizes_and_missing_entries_are_rejected(self):
        self.prepare()
        path = self.output/'python-wheels.resources.json'
        original = json.loads(path.read_text())
        for layout in ({}, {'demo':{'destination':'app','unpacked_bytes':True}},
                       {'demo':{'destination':'app','unpacked_bytes':-1}}):
            data = dict(original, layout=layout)
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError): self.load()

    def test_actual_volume_probe_does_not_create_target(self):
        target = self.root/'not-created'
        result = volume_for(target)
        self.assertTrue(result.id)
        self.assertGreater(result.free_bytes, 0)
        self.assertFalse(target.exists())

    def test_relative_destination_is_rejected(self):
        with self.assertRaises(ValueError): volume_for(Path('relative'))

    def test_wheel_tags_reject_wrong_python_and_architecture(self):
        for name in ('demo-1-cp313-cp313-win_amd64.whl','demo-1-cp312-cp312-win_arm64.whl',
                     'demo-1-py313-none-any.whl','demo-1-cp312-cp312-manylinux_x86_64.whl'):
            with self.subTest(name=name): self.assertFalse(supports_target(name))
        for name in ('demo-1-cp312-cp312-win_amd64.whl','demo-1-cp39-abi3-win_amd64.whl',
                     'demo-1-py3-none-any.whl','demo-1-py2.py3-none-any.whl'):
            with self.subTest(name=name): self.assertTrue(supports_target(name))

    def test_unpinned_requirement_is_rejected(self):
        self.lock.write_text('demo>=1.0\n')
        with self.assertRaises(ValueError): resolved_packages(self.lock)

if __name__ == '__main__':
    unittest.main()
