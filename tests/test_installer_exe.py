"""Frozen/source dispatch and bootstrap resource ownership checks."""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from installer import bootstrap, build_exe, entrypoints


class EntryTests(unittest.TestCase):
    def test_source_command_preserves_unicode_and_arguments(self):
        with patch.object(sys,'frozen',False,create=True):
            result=entrypoints.entry_command('installer.start',['--workspace','D:/中文 folder','--text','& $x'])
        self.assertEqual(result,[sys.executable,'-m','installer.start','--workspace','D:/中文 folder','--text','& $x'])

    def test_frozen_command_dispatches_without_system_python(self):
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable','D:/OdessySetup.exe'):
            result=entrypoints.entry_command('installer.launcher',['--install-dir','D:/Install'])
        self.assertEqual(result,['D:/OdessySetup.exe','--mode','launcher','--install-dir','D:/Install'])

    def test_independent_frozen_children_have_separate_extraction_lifetime(self):
        before=dict(os.environ)
        with patch.object(sys,'frozen',True,create=True): env=entrypoints.independent_environment()
        self.assertEqual(env['PYINSTALLER_RESET_ENVIRONMENT'],'1'); self.assertEqual(dict(os.environ),before)

    def test_source_children_do_not_require_bootloader_environment(self):
        with patch.object(sys,'frozen',False,create=True),patch.dict(os.environ,{},clear=True):
            self.assertNotIn('PYINSTALLER_RESET_ENVIRONMENT',entrypoints.independent_environment())

    def test_unknown_dispatch_is_refused(self):
        with self.assertRaises(ValueError): entrypoints.entry_command('unrelated.module',[])


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.repo=Path(self.temp.name)/'source'; self.bundle=self.repo/'bundled-tools'; self.bundle.mkdir(parents=True)
        self.local=Path(self.temp.name)/'local'; self.archive=self.bundle/'tool-server.zip'; self.archive.write_bytes(b'fixture')
        self.metadata={'sha256':'a'*64}; (self.bundle/'tool-server.resources.json').write_text(json.dumps(self.metadata))
        def validate(folder,repo):
            if (folder/'tool-server.zip').read_bytes()!=b'fixture': raise ValueError('corrupt archive')
            return folder/'tool-server.zip',self.metadata
        self.check=patch.object(bootstrap.application,'validate_bundle',side_effect=validate).start()
        self.addCleanup(patch.stopall)

    def test_resources_persist_outside_extraction_directory_and_are_reused(self):
        target=bootstrap.bundled_tools(self.repo,self.local)
        first=(target/'tool-server.zip').stat().st_mtime_ns
        self.assertFalse(self.repo in target.parents)
        self.assertEqual(bootstrap.bundled_tools(self.repo,self.local),target)
        self.assertEqual((target/'tool-server.zip').stat().st_mtime_ns,first)

    def test_corrupt_existing_resources_are_preserved_not_overwritten(self):
        target=bootstrap.bundled_tools(self.repo,self.local); archive=target/'tool-server.zip'; archive.write_bytes(b'corrupt')
        with self.assertRaises(ValueError): bootstrap.bundled_tools(self.repo,self.local)
        self.assertEqual(archive.read_bytes(),b'corrupt')

    def test_invalid_embedded_bundle_creates_no_resource_directory(self):
        self.archive.write_bytes(b'corrupt')
        with self.assertRaises(ValueError): bootstrap.bundled_tools(self.repo,self.local)
        self.assertFalse(self.local.exists())

    def test_failed_copy_cleans_only_new_owned_directory(self):
        target=self.local/'Odessy/installer-resources'/self.metadata['sha256']
        with patch.object(Path,'write_bytes',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): bootstrap.bundled_tools(self.repo,self.local)
        self.assertFalse(target.exists()); self.assertTrue(self.archive.exists())


class BuildInputTests(unittest.TestCase):
    def test_manifest_keeps_invoker_privileges_and_modern_windows_version_detection(self):
        import xml.etree.ElementTree as ET
        manifest=Path(__file__).resolve().parents[1]/'installer/windows.manifest'
        root=ET.fromstring(manifest.read_text(encoding='utf-8'))
        level=root.find('.//{urn:schemas-microsoft-com:asm.v3}requestedExecutionLevel')
        self.assertEqual(level.attrib,dict(level='asInvoker',uiAccess='false'))
        supported=root.findall('.//{urn:schemas-microsoft-com:compatibility.v1}supportedOS')
        self.assertIn('{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}',[item.attrib['Id'] for item in supported])

    def test_build_uses_selected_sources_not_checkout_caches_or_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            repo=Path(folder); installer=repo/'installer'; installer.mkdir(); bundle=repo/'bundle'; bundle.mkdir()
            (installer/'entry.py').write_text('pass'); (installer/'README.md').write_text('installer docs')
            (installer/'.env').write_text('SECRET=hidden'); cache=installer/'__pycache__'; cache.mkdir(); (cache/'secret.py').write_text('private')
            (bundle/'tool-server.zip').write_bytes(b'zip'); (bundle/'tool-server.resources.json').write_text('{}')
            tool=repo/'tools/package.json'; tool.parent.mkdir(); tool.write_text('{}')
            with patch.object(build_exe,'validate_bundle'),patch.object(build_exe,'source_snapshot',return_value={'src/odessy/__main__.py':b'pass'}),patch.object(build_exe,'source_files',return_value=[tool]):
                inputs=build_exe.build_inputs(repo,bundle)
            self.assertIn('installer/entry.py',inputs); self.assertIn('bundled-tools/tool-server.zip',inputs)
            self.assertFalse(any('.env' in name or '__pycache__' in name for name in inputs))

    def test_build_preview_does_not_create_output_or_install_a_builder(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'output'
            with patch.object(build_exe,'build_inputs',return_value={'fixture':b'x'}),patch.object(build_exe,'build') as build:
                code=build_exe.main(['--tool-bundle',folder,'--output-dir',str(output)])
            self.assertEqual(code,0); self.assertFalse(output.exists()); build.assert_not_called()


if __name__=='__main__': unittest.main()
