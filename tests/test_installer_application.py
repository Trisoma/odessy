"""Application assembly tests without real runtimes, network or model services."""
from __future__ import annotations
import hashlib
import base64
import importlib.util
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from installer.application import assemble,validate_bundle,source_snapshot
from installer.toolbundle import build_bundle
from installer.unpack import unpack,ArchiveError

class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.repo=self.root/'repo'
        for name in ('tools/src','src/odessy','config'):
            (self.repo/name).mkdir(parents=True,exist_ok=True)
        (self.repo/'src/odessy/__main__.py').write_text('print("fixture")')
        (self.repo/'src/odessy/__init__.py').write_text('')
        (self.repo/'src/odessy/contract.json').write_text('{}')
        (self.repo/'config/odessy.env').write_text('ODESSY_DEPLOYMENT=single\n')
        for name in ('pyproject.toml','README.md','LICENSE','THIRD-PARTY-NOTICES.md'):
            (self.repo/name).write_text('fixture')
        (self.repo/'tools/package.json').write_text(json.dumps({'version':'0.1.0'}))
        (self.repo/'tools/package-lock.json').write_text(json.dumps({'packages':{'':{'version':'0.1.0'}}}))
        (self.repo/'tools/tsconfig.json').write_text('{}')
        (self.repo/'tools/src/server.ts').write_text('export {};')
        self.versions={'python-runtime':{'version':'3.12.13','bits':64},'node-runtime':{'version':'22.23.0','bits':64}}
        self.runtime=self.root/'runtime'; self.deps=self.root/'deps'; self.bundle=self.root/'bundle'
        self.calls=[]
        self.print_patch=patch('builtins.print'); self.print_patch.start(); self.addCleanup(self.print_patch.stop)
        build_bundle(self.runtime,self.bundle,repo=self.repo,run=self.build_runner,validate=lambda p:self.versions)

    def build_runner(self,command,**kwargs):
        tools=kwargs['cwd']
        if 'ci' in command:
            for name in ('node_modules/typescript/bin/tsc','node_modules/.bin/tsc.cmd'):
                path=tools/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text('fixture')
        if '-p' in command:
            path=tools/'dist/src/server.js'; path.parent.mkdir(parents=True); path.write_text('export {};')
        return 'ok'

    def smoke_runner(self,command,**kwargs):
        self.calls.append((command,kwargs))
        return 'Directory entries: launcher-smoke.txt' if 'list .' in command else 'help'

    def assemble(self,**options):
        kwargs=dict(repo=self.repo,run=self.smoke_runner,validate=lambda p:self.versions,
                    dependency_check=lambda *a,**k:{'python':str(self.root/'private-python.exe'),'packages':{}})
        kwargs.update(options)
        return assemble(self.runtime,self.deps,self.bundle,self.root/'app',**kwargs)

    def test_assembly_keeps_source_layout_and_generated_launchers(self):
        settings=self.assemble()
        app=self.root/'app'
        for name in ('src/odessy/__main__.py','src/odessy/contract.json','config/odessy.env',
                     'tools/dist/src/server.js','launch.py','start-odessy.ps1','odessy-application.json'):
            self.assertTrue((app/name).is_file(),name)
        self.assertEqual(settings['status'],'ready')
        self.assertFalse((app/'.odessy-preparing').exists())
        self.assertGreaterEqual((app/'tools/dist/src/server.js').stat().st_mtime,(app/'tools/src/server.ts').stat().st_mtime)

    def test_smoke_isolated_from_user_services_config_and_commands(self):
        self.assemble()
        self.assertEqual(len(self.calls),2)
        for command,kwargs in self.calls:
            self.assertIn('-I',command)
            for key in ('ODESSY_CONFIG_FILE','ODESSY_SERVERS_FILE','ODESSY_COMMANDS_FILE'):
                self.assertTrue(Path(kwargs['env'][key]).parent==kwargs['cwd'])
        self.assertIn('--fake-model',self.calls[1][0])

    def test_corrupt_bundle_is_rejected_before_app_creation(self):
        (self.bundle/'tool-server.zip').write_bytes(b'corrupt')
        with self.assertRaises(ValueError): self.assemble()
        self.assertFalse((self.root/'app').exists())

    def test_changed_tool_source_requires_rebuild(self):
        (self.repo/'tools/src/server.ts').write_text('changed')
        with self.assertRaises(ValueError): self.assemble()
        self.assertFalse((self.root/'app').exists())

    def test_incomplete_metadata_cannot_omit_source_files(self):
        path=self.bundle/'tool-server.resources.json'; data=json.loads(path.read_text())
        data['source_sha256'].pop('tools/src/server.ts'); path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): validate_bundle(self.bundle,self.repo)

    def test_existing_application_directory_is_preserved(self):
        target=self.root/'app'; target.mkdir(); (target/'keep').write_text('keep')
        with self.assertRaises(ValueError): self.assemble()
        self.assertEqual((target/'keep').read_text(),'keep')

    def test_failed_node_smoke_removes_owned_application(self):
        with self.assertRaises(ValueError): self.assemble(run=lambda *a,**k:'no listing')
        self.assertFalse((self.root/'app').exists())
        self.assertTrue((self.bundle/'tool-server.zip').exists())

    def test_concurrent_application_source_edit_aborts(self):
        def changed(command,**kwargs):
            if 'list .' in command:
                (self.repo/'src/odessy/__main__.py').write_text('edited')
            return self.smoke_runner(command,**kwargs)
        with self.assertRaises(ValueError): self.assemble(run=changed)
        self.assertFalse((self.root/'app').exists())

    def test_unreviewed_source_asset_and_file_links_are_not_copied(self):
        (self.repo/'src/odessy/private.env').write_text('secret')
        with self.assertRaises(ValueError): source_snapshot(self.repo)

    def launcher(self):
        template=Path('installer/templates/launch.py')
        spec=importlib.util.spec_from_file_location('launcher_under_test',template)
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def test_launcher_requires_private_interpreter(self):
        self.assemble(); module=self.launcher()
        with patch.object(module,'__file__',str(self.root/'app/launch.py')), patch('sys.stderr'):
            with self.assertRaises(SystemExit): module.main(['--','--help'])

    def test_launcher_requires_explicit_workspace_for_actions(self):
        settings=self.assemble(); module=self.launcher()
        with patch.object(module,'__file__',str(self.root/'app/launch.py')), patch('sys.executable',settings['python']),patch('sys.stderr'):
            with self.assertRaises(SystemExit): module.main(['--','--fake-model','list .'])

    def test_launcher_preserves_odessy_config_and_forwards_quoted_arguments(self):
        self.check_launcher_forwarding(separator=True,encoded=True)

    def test_launcher_forwards_without_powershell_separator(self):
        self.check_launcher_forwarding(separator=False,encoded=True)

    def test_direct_launcher_forwards_without_separator(self):
        self.check_launcher_forwarding(separator=False,encoded=False)

    def check_launcher_forwarding(self,separator,encoded):
        settings=self.assemble(); module=self.launcher()
        workspace=self.root/'work space'; workspace.mkdir()
        original=Path.cwd(); original_path=list(__import__('sys').path)
        self.addCleanup(os.chdir,original); self.addCleanup(lambda: __import__('sys').path.__setitem__(slice(None),original_path))
        with patch.object(module,'__file__',str(self.root/'app/launch.py')), patch('sys.executable',settings['python']), \
             patch.dict(os.environ,{'ODESSY_MODEL_BACKEND':'deepseek','NODE_OPTIONS':'--require bad'}), \
             patch('sys.argv',['launch.py']), patch.object(module.runpy,'run_module') as run:
            # The template checks a Node binary exists but never executes it in this test.
            node=self.runtime/'node-runtime/node.exe'; node.parent.mkdir(parents=True); node.write_bytes(b'fixture')
            request='say \"quoted words\" 汉字'
            argv=['--workspace',str(workspace),*(['--'] if separator else []),'--fake-model',request,'--help']
            payload=base64.b64encode(json.dumps(argv,ensure_ascii=False).encode('utf-8')).decode('ascii')
            module.main(['--encoded-args',payload] if encoded else argv)
            self.assertEqual(__import__('sys').argv,['odessy','--fake-model',request,'--help'])
            self.assertEqual(os.environ['ODESSY_MODEL_BACKEND'],'deepseek')
            self.assertNotIn('NODE_OPTIONS',os.environ)
            self.assertEqual(Path.cwd(),workspace)
            run.assert_called_once_with('odessy',run_name='__main__')

    def test_malformed_encoded_argument_array_fails_cleanly(self):
        module=self.launcher()
        for payload in ('invalid***',base64.b64encode(b'{}').decode('ascii')):
            with patch('sys.stderr'):
                with self.assertRaises(SystemExit): module.main(['--encoded-args',payload])

if __name__=='__main__':
    unittest.main()
