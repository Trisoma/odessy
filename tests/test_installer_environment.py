"""Private dependency install and prebuilt tool release preparation tests."""
from __future__ import annotations
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from installer.dependencies import prepare_dependencies,DEFAULT_RESOURCES
from installer.process import child_env,owned_directory
from installer.space import load_resources,budget,Volume
from installer.toolbundle import build_bundle

class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.cache=self.root/'cache'; self.cache.mkdir()
        self.payload=self.root/'payload.whl'; self.payload.write_bytes(b'fixture')
        self.manifest,self.resources=load_resources(DEFAULT_RESOURCES)
        self.expected={a.id:a.version for a in self.manifest.artifacts}
        self.commands=[]
        self.print_patch=patch('builtins.print'); self.print_patch.start(); self.addCleanup(self.print_patch.stop)

    def command_runner(self,command,**kwargs):
        self.commands.append((command,kwargs))
        if any('importlib.metadata' in value for value in command):
            return json.dumps(self.expected)
        return ''

    def prepare(self,**kwargs):
        options=dict(download=lambda *a:SimpleNamespace(path=self.payload),run=self.command_runner,validate=lambda p:{})
        options.update(kwargs)
        return prepare_dependencies(self.root/'runtime',self.root/'environment',self.cache,**options)

    def test_dependency_commands_use_private_python_offline_hashes_and_final_path(self):
        report=self.prepare()
        install=next(c for c,_ in self.commands if 'install' in c)
        self.assertIn('--no-index',install); self.assertIn('--require-hashes',install)
        self.assertIn('--no-compile',install); self.assertIn('--isolated',install)
        self.assertEqual(Path(install[0]).resolve(),(self.root/'environment'/'venv'/'Scripts'/'python.exe').resolve())
        self.assertEqual(len(report['packages']),29)
        self.assertFalse((self.root/'environment'/'.odessy-preparing').exists())
        self.assertTrue((self.root/'environment'/'odessy-dependencies.json').is_file())
        self.assertEqual(list(self.cache.glob('.odessy-wheelhouse-*')),[])

    def test_install_failure_removes_only_owned_environment(self):
        keep=self.root/'keep'; keep.write_text('preserve')
        with self.assertRaises(ValueError): self.prepare(run=lambda *a,**k:(_ for _ in ()).throw(ValueError('failed')))
        self.assertFalse((self.root/'environment').exists()); self.assertEqual(keep.read_text(),'preserve')
        self.assertEqual(list(self.cache.glob('.odessy-wheelhouse-*')),[])

    def test_version_mismatch_removes_environment(self):
        with self.assertRaises(ValueError): self.prepare(run=lambda *a,**k:'{}')
        self.assertFalse((self.root/'environment').exists())

    def test_existing_environment_is_preserved(self):
        target=self.root/'environment'; target.mkdir(); (target/'keep').write_text('keep')
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual((target/'keep').read_text(),'keep'); self.assertEqual(self.commands,[])

    def test_download_failure_does_not_create_environment(self):
        with self.assertRaises(ValueError): self.prepare(download=lambda *a:(_ for _ in ()).throw(ValueError('offline')))
        self.assertFalse((self.root/'environment').exists())

    def test_child_environment_does_not_mutate_global_or_inherit_package_options(self):
        with patch.dict(os.environ,{'PIP_INDEX_URL':'private','NODE_OPTIONS':'--require private','NPM_CONFIG_USERCONFIG':'private'}):
            result=child_env(self.root/'runtime')
            self.assertNotIn('PIP_INDEX_URL',result); self.assertNotIn('NODE_OPTIONS',result)
            self.assertNotIn('NPM_CONFIG_USERCONFIG',result)
            self.assertEqual(os.environ['PIP_INDEX_URL'],'private')
            self.assertTrue(result['PATH'].startswith(str(self.root/'runtime'/'node-runtime')))

    def test_cancellation_cleans_owned_directory(self):
        target=self.root/'cancelled'
        with self.assertRaises(KeyboardInterrupt):
            with owned_directory(target): raise KeyboardInterrupt()
        self.assertFalse(target.exists())

    def test_changed_ownership_marker_prevents_cleanup_of_another_owner(self):
        target=self.root/'owned'
        with self.assertRaises(ValueError):
            with owned_directory(target):
                (target/'.odessy-preparing').write_text('different-owner')
                (target/'keep').write_text('preserve')
                raise ValueError('failed')
        self.assertEqual((target/'keep').read_text(),'preserve')

    def test_wheelhouse_copy_is_budgeted_on_cache_volume(self):
        groups=budget(self.manifest,self.resources['layout'],cache_dir=self.cache,app_dir=self.root/'app',
                      model_dir=self.root/'models',cache_copies=2,probe=lambda p:Volume(p.name,10**10))
        cache=next(g for g in groups if g['volume']=='cache')
        self.assertEqual(cache['payload_bytes'],self.manifest.download_bytes*2)

    def fixture_repo(self):
        repo=self.root/'repo'; (repo/'tools'/'src').mkdir(parents=True)
        (repo/'tools'/'src'/'server.ts').write_text('export const value=1;')
        (repo/'tools'/'package.json').write_text(json.dumps({'version':'0.1.0'}))
        (repo/'tools'/'package-lock.json').write_text(json.dumps({'packages':{'':{'version':'0.1.0'}}}))
        (repo/'tools'/'tsconfig.json').write_text('{}')
        (repo/'LICENSE').write_text('license'); (repo/'THIRD-PARTY-NOTICES.md').write_text('notices')
        (repo/'tools'/'.npmrc').write_text('private-token')
        (repo/'THIRD-PARTY-LICENSES').mkdir()
        (repo/'THIRD-PARTY-LICENSES'/'vendor.txt').write_text('vendor notice')
        return repo

    def tool_runner(self,command,**kwargs):
        self.commands.append((command,kwargs))
        tools=kwargs['cwd']
        if 'ci' in command:
            for name in ('node_modules/typescript/bin/tsc','node_modules/.bin/tsc.cmd'):
                p=tools/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('fixture')
        if '-p' in command:
            entry=tools/'dist'/'src'/'server.js'; entry.parent.mkdir(parents=True); entry.write_text('export {};')
        return 'ok'

    def test_tool_bundle_is_precompiled_and_excludes_user_npm_configuration(self):
        repo=self.fixture_repo()
        report=build_bundle(self.root/'runtime',self.root/'bundle',repo=repo,run=self.tool_runner,validate=lambda p:{})
        self.assertTrue(report['precompiled']); self.assertFalse(report['published'])
        with zipfile.ZipFile(self.root/'bundle'/'tool-server.zip') as archive:
            self.assertIn('tools/dist/src/server.js',archive.namelist())
            self.assertIn('tools/node_modules/.bin/tsc.cmd',archive.namelist())
            self.assertIn('tools/_licenses/LICENSE',archive.namelist())
            self.assertIn('tools/_licenses/THIRD-PARTY-LICENSES/vendor.txt',archive.namelist())
            self.assertNotIn('tools/.npmrc',archive.namelist())
        ci=next(c for c,_ in self.commands if 'ci' in c)
        self.assertIn('--ignore-scripts',ci); self.assertIn('--include=dev',ci)
        self.assertFalse((repo/'tools'/'node_modules').exists())

    def test_tool_build_failure_cleans_bundle_and_preserves_source(self):
        repo=self.fixture_repo()
        with self.assertRaises(ValueError):
            build_bundle(self.root/'runtime',self.root/'bundle',repo=repo,run=lambda *a,**k:(_ for _ in ()).throw(ValueError('npm failed')),validate=lambda p:{})
        self.assertFalse((self.root/'bundle').exists())
        self.assertTrue((repo/'tools'/'src'/'server.ts').exists())

    def test_tool_source_change_during_build_aborts_owned_output(self):
        repo=self.fixture_repo()
        def changed(command,**kwargs):
            self.tool_runner(command,**kwargs)
            if 'ci' in command:
                (repo/'tools'/'src'/'server.ts').write_text('edited during build')
            return 'ok'
        with self.assertRaises(ValueError):
            build_bundle(self.root/'runtime',self.root/'bundle',repo=repo,run=changed,validate=lambda p:{})
        self.assertFalse((self.root/'bundle').exists())
        self.assertEqual((repo/'tools'/'src'/'server.ts').read_text(),'edited during build')

if __name__=='__main__':
    unittest.main()
