"""Removal boundaries, fresh previews, partial failures and non-destructive UI."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from installer import uninstall, shortcuts, start
from installer.download import DownloadError
from installer.checkpoint import Step, run_steps, writer_lock


class RemovalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'installed';self.cache=self.base/'cache';self.cache.mkdir()
        (self.cache/'shared.blob').write_bytes(b'keep')
        run_steps(self.root,dict(install_dir=str(self.root),cache_dir=str(self.cache)),[Step('fixture',lambda:{},lambda:{})])
        for name,marker in uninstall.COMPONENTS.items():
            if name=='launcher':continue
            folder=self.root/name;folder.mkdir();(folder/'payload.bin').write_bytes(b'owned')
            if marker:(folder/marker).write_text(json.dumps(dict(schema_version=1)))
        (self.root/'models').mkdir();(self.root/'models/model.blob').write_bytes(b'model')
        (self.root/'config').mkdir();(self.root/'config/odessy.env').write_text('private config')
        (self.root/'my-note.txt').write_text('keep root note');self.workspace=self.base/'workspace';self.workspace.mkdir()
        (self.workspace/'user.txt').write_text('keep workspace')
        self.folders={key:self.base/key for key in ('desktop','programs')}
        for folder in self.folders.values():folder.mkdir()
        self.options=dict(folders=self.folders,probe=lambda root:[])

    def preview(self,**kwargs):return uninstall.plan(self.root,**self.options,**kwargs)
    def remove(self,preview,**kwargs):return uninstall.execute(self.root,preview['plan_sha256'],**self.options,**kwargs)

    def links(self):
        source=self.base/'Setup.exe';source.write_bytes(b'MZ fixture')
        def write(path,target,root):path.write_text('managed '+str(target))
        return shortcuts.create(self.root,source,folders=self.folders,writer=write)

    def test_preview_writes_nothing(self):
        before={str(path):path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        preview=self.preview();self.assertGreater(preview['bytes_to_remove'],0)
        self.assertEqual(before,{str(path):path.read_bytes() for path in self.root.rglob('*') if path.is_file()})
        self.assertFalse((self.root/uninstall.RECORD).exists())

    def test_default_removal_keeps_model_config_workspace_cache_and_root_notes(self):
        self.links();result=self.remove(self.preview());self.assertEqual(result['status'],'completed')
        self.assertFalse((self.root/'application').exists());self.assertFalse((self.root/'launcher').exists())
        self.assertEqual((self.root/'models/model.blob').read_bytes(),b'model')
        self.assertEqual((self.root/'config/odessy.env').read_text(),'private config')
        self.assertEqual((self.workspace/'user.txt').read_text(),'keep workspace')
        self.assertEqual((self.cache/'shared.blob').read_bytes(),b'keep')
        self.assertEqual((self.root/'my-note.txt').read_text(),'keep root note')
        self.assertTrue((self.root/'.installer/state.json').is_file())
        self.assertTrue(all(not (p/shortcuts.NAME).exists() for p in self.folders.values()))

    def test_delete_model_and_config_requires_matching_explicit_options(self):
        preview=self.preview(keep_models=False,keep_config=False)
        with self.assertRaisesRegex(ValueError,'变化'):self.remove(preview)
        self.remove(preview,keep_models=False,keep_config=False)
        self.assertFalse((self.root/'models').exists());self.assertFalse((self.root/'config/odessy.env').exists())

    def test_changed_file_aborts_before_starting_removal(self):
        preview=self.preview();(self.root/'runtime/payload.bin').write_bytes(b'changed content')
        with self.assertRaisesRegex(ValueError,'变化'):self.remove(preview)
        self.assertFalse((self.root/uninstall.RECORD).exists());self.assertTrue((self.root/'application').exists())

    def test_running_process_and_install_lock_prevent_deletion(self):
        with self.assertRaisesRegex(ValueError,'关闭'):uninstall.plan(self.root,folders=self.folders,probe=lambda root:[{'pid':123}])
        preview=self.preview()
        with writer_lock(self.root):
            with self.assertRaises(ValueError):self.remove(preview)
        self.assertFalse((self.root/uninstall.RECORD).exists())

    def test_modified_shortcut_preserved_and_unknown_shortcut_path_refused(self):
        record=self.links();path=Path(record['links'][0]);path.write_text('customized')
        preview=self.preview();self.assertIn(str(path),preview['retained_shortcuts'])
        self.remove(preview);self.assertEqual(path.read_text(),'customized')

    def test_forged_external_shortcut_path_is_never_deleted(self):
        record=self.links();marker=self.root/'launcher/odessy-shortcuts.json'
        outside=self.base/'outside.txt';outside.write_text('keep')
        record['link_sha256'][str(outside)]=shortcuts.digest(outside);marker.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError,'当前用户'):self.preview()
        self.assertEqual(outside.read_text(),'keep')

    def test_junction_or_link_anywhere_aborts_before_any_deletion(self):
        link=self.root/'runtime/escape'
        if os.name=='nt':
            shortcuts.powershell("$p=$env:ODESSY_SHORTCUT_JSON|ConvertFrom-Json; New-Item -ItemType Junction -Path $p.path -Value $p.target | Out-Null",payload=dict(path=str(link),target=str(self.workspace)))
        else:link.symlink_to(self.workspace,target_is_directory=True)
        try:
            with self.assertRaises((ValueError,OSError,DownloadError)):self.preview()
            self.assertTrue((self.root/'application').exists());self.assertEqual((self.workspace/'user.txt').read_text(),'keep workspace')
        finally:
            if os.name=='nt':os.rmdir(link)
            else:link.unlink()

    def test_partial_failure_is_recorded_blocks_launch_and_can_be_repreviewed(self):
        self.links();preview=self.preview();original=Path.unlink
        def fail(path,*args,**kwargs):
            if Path(path).resolve()==(self.root/'runtime/payload.bin').resolve():raise PermissionError('fixture locked file')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',fail):
            with self.assertRaises(PermissionError):self.remove(preview)
        record=json.loads((self.root/uninstall.RECORD).read_text());self.assertEqual(record['status'],'failed')
        with self.assertRaisesRegex(ValueError,'卸载'):start.inspect_installation(self.root)
        self.assertEqual(self.remove(self.preview())['status'],'completed')
        self.assertEqual((self.root/'models/model.blob').read_bytes(),b'model')

    def test_missing_ownership_marker_or_source_checkout_is_refused(self):
        (self.root/'runtime/odessy-runtime.json').unlink()
        with self.assertRaises(OSError):self.preview()
        (self.root/'.git').mkdir()
        with self.assertRaisesRegex(ValueError,'源码'):self.preview()

    def test_corrupt_uninstall_record_cannot_bypass_component_ownership_checks(self):
        (self.root/uninstall.RECORD).write_text('{}')
        with self.assertRaisesRegex(ValueError,'不一致'):self.preview()
        self.assertTrue((self.root/'application').is_dir())

    def test_shortcuts_cannot_revive_an_uninstalled_record(self):
        self.remove(self.preview())
        source=self.base/'setup.exe';source.write_bytes(b'MZ source')
        with self.assertRaisesRegex(ValueError,'卸载'):
            shortcuts.create(self.root,source,folders=self.folders)
        self.assertFalse((self.root/'launcher').exists())

    def test_installed_executable_cannot_delete_itself(self):
        with patch.object(uninstall.sys,'executable',str(self.root/'launcher/OdessyLauncher.exe')):
            with self.assertRaisesRegex(ValueError,'外'):self.preview()

    def test_cli_never_removes_without_preview_confirmation(self):
        with patch.object(uninstall,'execute') as remove:
            self.assertEqual(uninstall.main(['--install-dir',str(self.root),'--remove']),1)
        remove.assert_not_called()


class RemovalUiTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from installer.uninstall_ui import Uninstaller
        try:self.root=tk.Tk();self.root.withdraw()
        except tk.TclError as error:self.skipTest(str(error))
        self.calls=[];self.allow=False
        self.preview=dict(keep_models=True,keep_config=True,plan_sha256='fixture',components=['application'],bytes_to_remove=12,shortcuts=[],retained_shortcuts=[])
        def remove(*args,**kwargs):self.calls.append(kwargs);return dict(removed_files=1,removed_shortcuts=0)
        self.ui=Uninstaller(self.root,Path('D:/fixture'),preview=lambda *args,**kwargs:dict(self.preview,**kwargs),remove=remove,confirm=lambda message:self.allow)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        import gc
        if self.ui.thread:self.ui.thread.join(3)
        try:
            for callback in self.root.tk.call('after','info'):self.root.after_cancel(callback)
            self.root.destroy()
        except Exception:pass
        self.ui=None;self.root=None
        gc.collect()

    def pump(self):
        end=time.monotonic()+3
        while self.ui.busy and time.monotonic()<end:self.root.update();time.sleep(.01)
        self.assertFalse(self.ui.busy)

    def test_preview_and_declined_confirmation_never_remove(self):
        self.assertTrue(self.ui.keep_models.get());self.ui.preview();self.pump();self.ui.remove()
        self.assertEqual(self.calls,[])

    def test_option_changes_invalidate_preview_and_confirmed_removal_uses_selection(self):
        self.ui.preview();self.pump();self.ui.keep_models.set(False);self.assertIsNone(self.ui.plan)
        self.ui.remove();self.assertEqual(self.calls,[])
        self.ui.preview();self.pump();self.allow=True;self.ui.remove();self.pump()
        self.assertEqual(self.calls,[dict(keep_models=False,keep_config=True)])


if __name__=='__main__':unittest.main()
