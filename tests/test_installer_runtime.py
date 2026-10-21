"""Runtime archive and atomic private-directory tests, using synthetic archives."""
from __future__ import annotations
import hashlib
import io
import json
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from installer.manifest import Artifact, Manifest
from installer.download import IntegrityError
from installer.runtime import load_release, destination, prepare_runtime, probe_runtime, RuntimePreparationError
from installer.unpack import ArchiveError, relative_member, unpack

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, rows, *, kind='zip'):
        path = self.root/('fixture.zip' if kind == 'zip' else 'fixture.tar.gz')
        if kind == 'zip':
            with zipfile.ZipFile(path,'w') as archive:
                for name,data in rows:
                    archive.writestr(name,data)
        else:
            with tarfile.open(path,'w:gz') as archive:
                for name,data in rows:
                    info=tarfile.TarInfo(name)
                    info.size=len(data)
                    archive.addfile(info,io.BytesIO(data))
        artifact=Artifact('python-runtime','3.12.13','https://example.test/fixed/archive',path.stat().st_size,hashlib.sha256(path.read_bytes()).hexdigest())
        return path,artifact

    def test_valid_zip_and_tar_extract_into_new_directory(self):
        for kind in ('zip','tar.gz'):
            with self.subTest(kind=kind):
                path,artifact=self.archive([('python/python.exe',b'fake')],kind=kind)
                target=self.root/kind
                unpack(path,artifact,target,kind=kind,prefix='python',unpacked_bytes=4)
                self.assertEqual((target/'python.exe').read_bytes(),b'fake')

    def test_traversal_windows_names_and_links_are_rejected(self):
        names=['python/../escape','/python/escape','C:/python/escape','python/C:escape',
               'python/dir'+chr(92)+'escape','python/NUL.txt','python/name.','python/name ',
               'wrong/file','python/COM1','python/COM¹','python/CON .txt','python/bad?name','python/'+chr(0)+'bad']
        for name in names:
            with self.subTest(name=name):
                with self.assertRaises(ArchiveError): relative_member(name,'python')

    def test_bad_member_prevents_any_extraction(self):
        path,artifact=self.archive([('python/good',b'ok'),('python/../outside',b'bad')])
        target=self.root/'output'
        with self.assertRaises(ArchiveError): unpack(path,artifact,target,kind='zip',prefix='python',unpacked_bytes=5)
        self.assertFalse(target.exists())
        self.assertFalse((self.root/'outside').exists())

    def test_case_collision_is_rejected(self):
        path,artifact=self.archive([('python/File',b'a'),('python/file',b'b')])
        with self.assertRaises(ArchiveError): unpack(path,artifact,self.root/'out',kind='zip',prefix='python',unpacked_bytes=2)
        self.assertFalse((self.root/'out').exists())

    def test_tar_symlink_and_hardlink_are_rejected(self):
        for link_type in (tarfile.SYMTYPE,tarfile.LNKTYPE):
            path=self.root/'link.tar.gz'
            with tarfile.open(path,'w:gz') as archive:
                info=tarfile.TarInfo('python/link'); info.type=link_type; info.linkname='../outside'
                archive.addfile(info)
            artifact=Artifact('python-runtime','3.12.13','https://example.test/fixed',path.stat().st_size,hashlib.sha256(path.read_bytes()).hexdigest())
            with self.assertRaises(ArchiveError): unpack(path,artifact,self.root/'out',kind='tar.gz',prefix='python',unpacked_bytes=1)

    def test_zip_symlink_is_rejected(self):
        path=self.root/'link.zip'
        with zipfile.ZipFile(path,'w') as archive:
            info=zipfile.ZipInfo('python/link'); info.create_system=3; info.external_attr=(stat.S_IFLNK|0o777)<<16
            archive.writestr(info,'../outside')
        artifact=Artifact('python-runtime','3.12.13','https://example.test/fixed',path.stat().st_size,hashlib.sha256(path.read_bytes()).hexdigest())
        with self.assertRaises(ArchiveError): unpack(path,artifact,self.root/'out',kind='zip',prefix='python',unpacked_bytes=10)

    def test_wrong_size_or_hash_never_produces_runtime(self):
        path,artifact=self.archive([('python/file',b'data')])
        for size in (3,5):
            with self.assertRaises(ArchiveError): unpack(path,artifact,self.root/'out',kind='zip',prefix='python',unpacked_bytes=size)
            self.assertFalse((self.root/'out').exists())
        path.write_bytes(b'corrupt')
        with self.assertRaises(IntegrityError): unpack(path,artifact,self.root/'out',kind='zip',prefix='python',unpacked_bytes=4)
        self.assertFalse((self.root/'out').exists())

    def test_non_archive_with_matching_hash_is_reported_cleanly(self):
        path=self.root/'invalid.zip'; path.write_bytes(b'not an archive')
        artifact=Artifact('python-runtime','3.12.13','https://example.test/fixed',path.stat().st_size,hashlib.sha256(path.read_bytes()).hexdigest())
        with self.assertRaises(ArchiveError): unpack(path,artifact,self.root/'out',kind='zip',prefix='python',unpacked_bytes=1)
        self.assertFalse((self.root/'out').exists())

    def test_existing_extraction_and_runtime_directories_are_preserved(self):
        path,artifact=self.archive([('python/file',b'data')])
        target=self.root/'existing'; target.mkdir(); (target/'keep').write_text('preserve')
        with self.assertRaises(ArchiveError): unpack(path,artifact,target,kind='zip',prefix='python',unpacked_bytes=4)
        with self.assertRaises(RuntimePreparationError): destination(target)
        self.assertEqual((target/'keep').read_text(),'preserve')

    def test_root_relative_and_protected_runtime_targets_are_rejected(self):
        for path in (Path('relative'),Path(self.root.anchor)):
            with self.assertRaises(RuntimePreparationError): destination(path)
        with patch.dict(os.environ,{'WINDIR':str(self.root)}):
            with self.assertRaises(RuntimePreparationError): destination(self.root/'forbidden')

    def test_release_metadata_binds_download_manifest(self):
        release=Path('installer/releases/windows-x64-runtimes/runtime.resources.json')
        manifest,resource=load_release(release)
        self.assertEqual({a.id for a in manifest.artifacts},{'python-runtime','node-runtime'})
        changed=self.root/'runtime.resources.json'
        data=json.loads(release.read_text()); data['download_manifest']='../escape.json'
        changed.write_text(json.dumps(data))
        with self.assertRaises(ValueError): load_release(changed)

    def fixture_runtime(self):
        path,artifact=self.archive([('python/python.exe',b'fake')],kind='tar.gz')
        return Manifest('fixture',(artifact,)), {'runtimes':{'python-runtime':{'format':'tar.gz','prefix':'python','unpacked_bytes':4}}}, path

    def test_probe_failure_cleans_staging_and_preserves_final_target(self):
        manifest,resources,path=self.fixture_runtime()
        target=self.root/'runtime'
        with patch('installer.runtime.platform.system',return_value='Windows'), patch('installer.runtime.platform.machine',return_value='AMD64'):
            with self.assertRaises(ValueError):
                prepare_runtime(manifest,resources,target,self.root/'cache',download=lambda *args:SimpleNamespace(path=path),
                                probe=lambda *args:(_ for _ in ()).throw(ValueError('probe failed')))
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.glob('.odessy-runtime-*')),[])

    def test_success_completes_inherited_directory_without_promotion(self):
        manifest,resources,path=self.fixture_runtime()
        target=self.root/'runtime'
        with patch('installer.runtime.platform.system',return_value='Windows'), patch('installer.runtime.platform.machine',return_value='AMD64'):
            result=prepare_runtime(manifest,resources,target,self.root/'cache',download=lambda *args:SimpleNamespace(path=path),
                                   probe=lambda *args:{'version':'3.12.13','bits':64})
        self.assertTrue((target/'python-runtime'/'python.exe').exists())
        self.assertTrue((target/'odessy-runtime.json').exists())
        self.assertEqual(result['python-runtime']['bits'],64)
        self.assertEqual(list(self.root.glob('.odessy-runtime-*')),[])

    def test_probe_uses_isolated_arguments_and_removes_injection_variables(self):
        report=SimpleNamespace(returncode=0,stdout='{"version":"3.12.13","bits":64}')
        with patch.dict(os.environ,{'PYTHONPATH':'private','NODE_OPTIONS':'--require private'}), patch('installer.runtime.subprocess.run',return_value=report) as run:
            probe_runtime(self.root,'python-runtime','3.12.13')
            args,kwargs=run.call_args
            self.assertIn('-I',args[0])
            self.assertNotIn('PYTHONPATH',kwargs['env'])
            self.assertNotIn('NODE_OPTIONS',kwargs['env'])
            self.assertFalse(kwargs.get('shell',False))

    def test_download_failure_does_not_create_runtime_parent(self):
        manifest,resources,path=self.fixture_runtime()
        target=self.root/'new-parent'/'runtime'
        with patch('installer.runtime.platform.system',return_value='Windows'), patch('installer.runtime.platform.machine',return_value='AMD64'):
            with self.assertRaises(ValueError):
                prepare_runtime(manifest,resources,target,self.root/'cache',
                    download=lambda *args:(_ for _ in ()).throw(ValueError('download failed')))
        self.assertFalse(target.parent.exists())

    def test_target_created_during_download_is_preserved(self):
        manifest,resources,path=self.fixture_runtime()
        target=self.root/'runtime'
        def create_conflict(*args):
            target.mkdir()
            (target/'keep').write_text('preserve')
            return SimpleNamespace(path=path)
        with patch('installer.runtime.platform.system',return_value='Windows'), patch('installer.runtime.platform.machine',return_value='AMD64'):
            with self.assertRaises(RuntimePreparationError):
                prepare_runtime(manifest,resources,target,self.root/'cache',download=create_conflict,probe=lambda *args:{'version':'3.12.13','bits':64})
        self.assertEqual((target/'keep').read_text(),'preserve')
        self.assertEqual(list(self.root.glob('.odessy-runtime-*')),[])

    def test_non_windows_preparation_is_rejected(self):
        manifest,resources,path=self.fixture_runtime()
        with patch('installer.runtime.platform.system',return_value='Linux'):
            with self.assertRaises(RuntimePreparationError): prepare_runtime(manifest,resources,self.root/'out',self.root/'cache')

if __name__ == '__main__':
    unittest.main()
