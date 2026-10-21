"""Persistent launcher ownership, conflict handling and real Windows link checks."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from installer import shortcuts, exe_entry
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
    def test_real_wsh_missing_link_repair(self):
        first=shortcuts.create(self.root,self.source,folders=self.folders)
        target=Path(first['executable']);stamp=target.stat().st_mtime_ns
        link=self.folders['desktop']/shortcuts.NAME;link.unlink()
        result=shortcuts.create(self.root,target,folders=self.folders)
        self.assertTrue(link.exists());self.assertFalse(result['existing'])
        self.assertEqual(target.stat().st_mtime_ns,stamp)
        self.assertEqual(shortcuts.digest(link),result['link_sha256'][str(link)])

    def test_foreign_link_is_preserved_before_any_launcher_copy(self):
        link=self.folders['desktop']/shortcuts.NAME;link.write_text('foreign')
        with self.assertRaisesRegex(ValueError,'同名'):self.create()
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
        with self.assertRaisesRegex(ValueError,'变化'):self.create()
        self.assertEqual(link.read_text(),'user changed')

    def test_selection_and_moved_install_are_rejected(self):
        with self.assertRaises(ValueError):self.create(desktop=False,start_menu=False)
        with patch.object(shortcuts,'read_state',return_value=dict(status='completed',summary=dict(workflow_complete=True),plan=dict(install_dir=str(self.base/'other')))):
            with self.assertRaisesRegex(ValueError,'原位置'):self.create()
        self.assertFalse((self.root/'launcher').exists())

    def test_different_executable_does_not_replace_managed_launcher(self):
        result=self.create();self.source.write_bytes(b'MZ changed')
        with self.assertRaisesRegex(ValueError,'版本不同'):self.create()
        self.assertEqual(Path(result['executable']).read_bytes(),b'MZ fixture executable')

    def test_low_space_does_not_create_launchers_or_links(self):
        from types import SimpleNamespace
        with patch.object(shortcuts.shutil,'disk_usage',return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(ValueError,'空间不足'):self.create()
        self.assertFalse((self.root/'launcher').exists())

    def test_close_during_background_creation_waits_for_result_without_false_cancellation(self):
        from installer.wizard_controller import Controller
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
            self.assertEqual(exe_entry.installed_launcher_root(),self.root)
        with patch.object(exe_entry.sys,'frozen',True,create=True),patch.object(exe_entry.sys,'executable',str(self.source)):
            self.assertIsNone(exe_entry.installed_launcher_root())

    @unittest.skipUnless(os.name=='nt','Windows Shell shortcut')
    def test_actual_wsh_link_contains_exact_quoted_unicode_arguments(self):
        result=shortcuts.create(self.root,self.source,folders=self.folders)
        link=self.folders['desktop']/shortcuts.NAME
        data=json.loads(shortcuts.powershell("$p=$env:ODESSY_SHORTCUT_JSON|ConvertFrom-Json; $s=(New-Object -ComObject WScript.Shell).CreateShortcut($p.path); @{target=$s.TargetPath;arguments=$s.Arguments;working=$s.WorkingDirectory}|ConvertTo-Json -Compress",payload=dict(path=str(link))))
        import subprocess
        self.assertEqual(data['target'],result['executable'])
        self.assertEqual(data['arguments'],subprocess.list2cmdline(['--mode','launcher','--install-dir',str(self.root)]))
        self.assertEqual(data['working'],str(self.root/'launcher'))
        self.assertTrue(self.create()['existing'])


if __name__=='__main__':unittest.main()
