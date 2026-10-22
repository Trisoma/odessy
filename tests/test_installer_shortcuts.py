"""Persistent launcher ownership, conflict handling and real Windows link checks."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from installer import shortcuts, exe_entry, maintenance, shelllink
from installer.checkpoint import Step, run_steps


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'安装 $ folder';self.source=self.base/'Setup.exe'
        self.source.write_bytes(b'MZ fixture executable')
        self.folders={key:self.base/key for key in ('desktop','programs')}
        for folder in self.folders.values():folder.mkdir()
        run_steps(self.root,dict(install_dir=str(self.root)),[Step('fixture',lambda:{},lambda:{})])
        self.state=(self.root/'.installer/state.json').read_bytes()

    def fake_writer(self,path,target,root):
        with path.open('xb') as stream:stream.write(json.dumps(dict(target=str(target),root=str(root))).encode())

    def create(self,**kwargs):
        return shortcuts.create(self.root,self.source,folders=self.folders,writer=kwargs.pop('writer',self.fake_writer),**kwargs)

    def test_preview_is_read_only_and_accounts_for_the_stable_executable(self):
        result=shortcuts.plan(self.root,self.source,folders=self.folders)
        self.assertFalse(result['existing']);self.assertEqual(len(result['links']),2)
        self.assertFalse((self.root/'launcher').exists())
        self.assertTrue(all(not list(folder.iterdir()) for folder in self.folders.values()))

    def test_creation_uses_the_recorded_resolved_installation_path(self):
        spelling=self.root/'..'/self.root.name
        result=shortcuts.create(spelling,self.source,folders=self.folders,writer=self.fake_writer)
        for link in result['links']:
            self.assertEqual(json.loads(Path(link).read_text())['root'],result['install_dir'])

    def test_maintenance_recognizes_equivalent_installation_arguments(self):
        self.create()
        target=self.root.resolve()/'launcher/OdessyLauncher.exe'
        spellings=[self.root/'..'/self.root.name]
        if os.name=='nt':spellings.append(Path(str(self.root).upper()))
        for spelling in spellings:
            with self.subTest(spelling=spelling):
                info=dict(target=str(target),arguments=shortcuts.launcher_arguments(spelling),working=str(target.parent))
                with patch.object(maintenance.shelllink,'read',return_value=info):
                    record,retained=maintenance.launcher_record(self.root.resolve(),self.source,folders=self.folders)
                self.assertEqual(len(record['links']),2);self.assertEqual(retained,[])

    def test_maintenance_preserves_links_with_extra_arguments_or_other_locations(self):
        self.create();target=self.root.resolve()/'launcher/OdessyLauncher.exe'
        original=dict(target=str(target),arguments=shortcuts.launcher_arguments(self.root.resolve()),working=str(target.parent))
        variants=[dict(original,arguments=original['arguments']+' --mode uninstall'),
                  dict(original,arguments=shortcuts.launcher_arguments(self.base/'other')),
                  dict(original,target=str(self.source)),dict(original,working=str(self.base))]
        for info in variants:
            with self.subTest(info=info),patch.object(maintenance.shelllink,'read',return_value=info):
                record,retained=maintenance.launcher_record(self.root.resolve(),self.source,folders=self.folders)
                self.assertEqual(record['links'],[]);self.assertEqual(len(retained),2)

    def test_copy_survives_source_removal_and_identical_creation_is_idempotent(self):
        first=self.create();target=Path(first['executable']);timestamp=target.stat().st_mtime_ns
        self.assertTrue(self.create()['existing']);self.assertEqual(target.stat().st_mtime_ns,timestamp)
        self.source.unlink();self.assertEqual(target.read_bytes(),b'MZ fixture executable')
        self.assertEqual((self.root/'.installer/state.json').read_bytes(),self.state)

    def test_missing_link_is_recreated_without_replacing_launcher(self):
        first=self.create();target=Path(first['executable']);stamp=target.stat().st_mtime_ns
        link=self.folders['desktop']/shortcuts.NAME;link.unlink()
        other=self.folders['programs']/shortcuts.NAME;content=other.read_bytes()
        result=self.create()
        self.assertTrue(link.is_file());self.assertFalse(result['existing'])
        self.assertEqual(target.stat().st_mtime_ns,stamp);self.assertEqual(other.read_bytes(),content)
        self.assertTrue(self.create()['existing'])

    def test_missing_unselected_link_is_not_recreated(self):
        self.create();link=self.folders['desktop']/shortcuts.NAME;link.unlink()
        self.assertTrue(self.create(desktop=False)['existing']);self.assertFalse(link.exists())

    def test_adding_start_menu_location_preserves_desktop(self):
        self.create(start_menu=False)
        link=self.folders['desktop']/shortcuts.NAME;content=link.read_bytes()
        self.create();self.assertEqual(link.read_bytes(),content)
        self.assertTrue((self.folders['programs']/shortcuts.NAME).is_file())

    def test_failed_link_repair_preserves_original_marker_and_launcher(self):
        first=self.create();target=Path(first['executable']);marker=target.parent/shortcuts.MARKER
        old=marker.read_bytes();exe=target.read_bytes()
        for folder in self.folders.values():(folder/shortcuts.NAME).unlink()
        def fail(path,target,root):
            if path.parent==self.folders['programs']:raise OSError('fixture failure')
            self.fake_writer(path,target,root)
        with self.assertRaises(OSError):self.create(writer=fail)
        self.assertEqual(marker.read_bytes(),old);self.assertEqual(target.read_bytes(),exe)
        self.assertFalse((self.folders['desktop']/shortcuts.NAME).exists())

    @unittest.skipUnless(os.name=='nt','Windows Shell shortcut')
    def test_real_unicode_link_missing_link_repair(self):
        first=shortcuts.create(self.root,self.source,folders=self.folders)
        target=Path(first['executable']);stamp=target.stat().st_mtime_ns
        link=self.folders['desktop']/shortcuts.NAME;link.unlink()
        result=shortcuts.create(self.root,target,folders=self.folders)
        self.assertTrue(link.exists());self.assertFalse(result['existing'])
        self.assertEqual(target.stat().st_mtime_ns,stamp)
        self.assertEqual(shortcuts.digest(link),result['link_sha256'][str(link)])

    def test_foreign_link_is_preserved_before_any_launcher_copy(self):
        link=self.folders['desktop']/shortcuts.NAME;link.write_text('foreign')
        with self.assertRaisesRegex(ValueError,'A shortcut with the same name already exists'):self.create()
        self.assertEqual(link.read_text(),'foreign');self.assertFalse((self.root/'launcher').exists())

    def test_failure_on_second_link_rolls_back_only_this_attempt(self):
        def fail(path,target,root):
            if path.parent==self.folders['programs']:raise OSError('fixture failure')
            self.fake_writer(path,target,root)
        with self.assertRaises(OSError):self.create(writer=fail)
        self.assertFalse((self.root/'launcher').exists());self.assertFalse((self.folders['desktop']/shortcuts.NAME).exists())
        self.assertEqual((self.root/'.installer/state.json').read_bytes(),self.state)

    def test_racing_foreign_link_is_not_overwritten_or_removed(self):
        def race(path,target,root):
            path.write_text('appeared after preview');self.fake_writer(path,target,root)
        with self.assertRaises(FileExistsError):self.create(writer=race)
        self.assertEqual((self.folders['desktop']/shortcuts.NAME).read_text(),'appeared after preview')
        self.assertFalse((self.root/'launcher').exists())

    def test_changed_managed_link_is_preserved_on_retry(self):
        self.create();link=self.folders['desktop']/shortcuts.NAME;link.write_text('user changed')
        with self.assertRaisesRegex(ValueError,'An existing shortcut changed or belongs to'):self.create()
        self.assertEqual(link.read_text(),'user changed')

    def test_selection_and_moved_install_are_rejected(self):
        with self.assertRaises(ValueError):self.create(desktop=False,start_menu=False)
        with patch.object(shortcuts,'read_state',return_value=dict(status='completed',summary=dict(workflow_complete=True),plan=dict(install_dir=str(self.base/'other')))):
            with self.assertRaisesRegex(ValueError,'Complete the application installation at the'):self.create()
        self.assertFalse((self.root/'launcher').exists())

    def test_different_executable_does_not_replace_managed_launcher(self):
        result=self.create();self.source.write_bytes(b'MZ changed')
        with self.assertRaisesRegex(ValueError,'An existing launcher entry is a different'):self.create()
        self.assertEqual(Path(result['executable']).read_bytes(),b'MZ fixture executable')

    def test_low_space_does_not_create_launchers_or_links(self):
        from types import SimpleNamespace
        with patch.object(shortcuts.shutil,'disk_usage',return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(ValueError,'Not enough disk space to save the standalone'):self.create()
        self.assertFalse((self.root/'launcher').exists())

    def test_close_during_background_creation_waits_for_result_without_false_cancellation(self):
        from installer.ui.wizard_controller import Controller
        from types import SimpleNamespace
        import threading
        entered=threading.Event();release=threading.Event()
        def create(payload):
            entered.set();release.wait(3);return {'executable':'persistent.exe'}
        controller=Controller(SimpleNamespace(shortcuts=create))
        controller.start('shortcuts',{})
        try:
            self.assertTrue(entered.wait(2));controller.request_cancel();release.set();controller.thread.join(3)
            messages=[]
            while not controller.messages.empty():messages.append(controller.messages.get_nowait())
            self.assertEqual(messages[-1]['kind'],'finished');self.assertFalse(controller.busy)
        finally:
            release.set();controller.thread.join(3)

    def test_direct_installed_executable_defaults_to_its_own_launcher(self):
        result=self.create()
        with patch.object(exe_entry.sys,'frozen',True,create=True),patch.object(exe_entry.sys,'executable',result['executable']):
            self.assertEqual(exe_entry.installed_launcher_root(),self.root.resolve())
        with patch.object(exe_entry.sys,'frozen',True,create=True),patch.object(exe_entry.sys,'executable',str(self.source)):
            self.assertIsNone(exe_entry.installed_launcher_root())

    @unittest.skipUnless(os.name=='nt','Windows Shell shortcut')
    def test_actual_link_contains_exact_quoted_unicode_arguments(self):
        result=shortcuts.create(self.root,self.source,folders=self.folders)
        link=self.folders['desktop']/shortcuts.NAME
        data=shelllink.read(link)
        import subprocess
        self.assertEqual(data['target'],result['executable'])
        self.assertEqual(data['arguments'],subprocess.list2cmdline(['--mode','launcher','--install-dir',result['install_dir']]))
        self.assertEqual(data['working'],str((self.root/'launcher').resolve()))
        self.assertTrue(self.create()['existing'])

    @unittest.skipUnless(os.name=='nt','Windows Shell shortcut')
    def test_non_ansi_paths_survive_maintenance_and_shortcut_repair(self):
        self.root=self.base/'安装 🚀 $ folder'
        run_steps(self.root,dict(install_dir=str(self.root)),[Step('fixture',lambda:{},lambda:{})])
        first=shortcuts.create(self.root,self.source,folders=self.folders)
        for link in first['links']:
            self.assertEqual(shelllink.read(Path(link))['target'],first['executable'])
        record,retained=maintenance.launcher_record(self.root.resolve(),self.source,folders=self.folders)
        self.assertEqual(len(record['links']),2);self.assertEqual(retained,[])
        (self.folders['desktop']/shortcuts.NAME).unlink()
        result=shortcuts.create(self.root,self.source,folders=self.folders)
        self.assertFalse(result['existing']);self.assertTrue(shortcuts.create(self.root,self.source,folders=self.folders)['existing'])

    @unittest.skipUnless(os.name=='nt','Windows PowerShell environment')
    def test_powershell_uses_only_builtin_modules_and_preserves_analysis_cache(self):
        import subprocess
        with patch.dict(os.environ,{'PSModulePath':'untrusted modules','PSModuleAnalysisCachePath':'cache-fixture'}), \
             patch.object(shortcuts.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'ready','')) as run:
            self.assertEqual(shortcuts.powershell('Write-Output ready'),'ready')
        child=run.call_args.kwargs['env']
        self.assertEqual(child['PSModulePath'],str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/Modules'))
        self.assertEqual({key.upper():value for key,value in child.items()}['PSMODULEANALYSISCACHEPATH'],'cache-fixture')

    @unittest.skipUnless(os.name=='nt','Windows PowerShell environment')
    def test_powershell_does_not_repeat_side_effects_after_timeout(self):
        import subprocess
        with patch.object(shortcuts.subprocess,'run',side_effect=subprocess.TimeoutExpired('fixture',30)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):shortcuts.powershell('New-Item fixture')
        self.assertEqual(run.call_count,1)


if __name__=='__main__':unittest.main()
