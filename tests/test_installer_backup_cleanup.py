"""Discard backup previews, exact removals, partial failure and recovery guards."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import test_installer_maintenance as fixtures
from installer import backup_cleanup as cleanup,maintenance
from installer.checkpoint import read_state,fingerprint


class BackupCleanupTests(unittest.TestCase):
    assemble=fixtures.MaintenanceTests.assemble
    verify=fixtures.MaintenanceTests.verify
    planner=fixtures.MaintenanceTests.planner
    apply=fixtures.MaintenanceTests.apply
    def setUp(self):
        fixtures.MaintenanceTests.setUp(self)
        result=self.apply();self.folder=self.root/'.installer/updates'/result['id']
        self.state=read_state(self.root)
    def preview(self):return cleanup.plan(self.root,probe=lambda root:[])
    def remove(self):
        preview=self.preview();return cleanup.execute(self.root,preview['plan_sha256'],probe=lambda root:[])
    def test_preview_is_read_only_and_reports_only_owned_backups(self):
        (self.folder/'user-note').write_text('keep note')
        before=(self.root/maintenance.JOURNAL).read_bytes();p=self.preview()
        self.assertEqual(p['file_count'],2);self.assertGreater(p['bytes_to_remove'],0)
        self.assertEqual(p['retained_transaction_entries'],['user-note'])
        self.assertEqual((self.root/maintenance.JOURNAL).read_bytes(),before)
        self.assertTrue((self.folder/'backup-application/old.txt').exists())
    def test_removes_only_backups_and_keeps_installation_data_and_other_records(self):
        (self.folder/'user-note').write_text('keep note');(self.folder/'service').mkdir();(self.folder/'service/log').write_text('keep log')
        result=self.remove();self.assertEqual(result['removed_files'],2)
        self.assertFalse((self.folder/'backup-application').exists());self.assertFalse((self.folder/'backup-launcher').exists())
        for p in ('application/new.txt','launcher/OdessyLauncher.exe','models/model','config/odessy.env'):
            self.assertTrue((self.root/p).exists())
        self.assertEqual(read_state(self.root),self.state)
        self.assertEqual((self.folder/'user-note').read_text(),'keep note')
        self.assertEqual((self.folder/'service/log').read_text(),'keep log')
        maintenance.require_idle(self.root)
        with self.assertRaisesRegex(ValueError,'已确认清理'):maintenance.recover(self.root,probe=lambda root:[])
        self.assertEqual(maintenance.journal(self.root)['status'],'completed')
        self.assertEqual(self.remove()['removed_files'],0)
    def test_backup_changed_before_preview_is_preserved(self):
        (self.folder/'backup-application/notes').write_text('keep')
        with self.assertRaisesRegex(ValueError,'备份已变化'):self.preview()
        self.assertFalse(maintenance.journal(self.root).get('rollback_discarded',False))
    def test_changes_after_preview_require_another_confirmation(self):
        p=self.preview();(self.folder/'backup-application/old.txt').write_text('changed backup')
        with self.assertRaises(ValueError):cleanup.execute(self.root,p['plan_sha256'],probe=lambda root:[])
        self.assertTrue((self.folder/'backup-launcher/old.txt').exists())
    def test_wrong_confirmation_cannot_discard_rollback(self):
        with self.assertRaisesRegex(ValueError,'重新查看'):cleanup.execute(self.root,'wrong',probe=lambda root:[])
        self.assertFalse(maintenance.journal(self.root).get('rollback_discarded',False))
    def test_active_process_or_maintenance_blocks_removal(self):
        with self.assertRaisesRegex(ValueError,'关闭'):cleanup.plan(self.root,probe=lambda root:[{'pid':1}])
        data=maintenance.journal(self.root);data['status']='repairing';maintenance._save(self.root,data)
        with self.assertRaisesRegex(ValueError,'维护'):self.preview()
    def test_partial_failure_is_recorded_and_rollback_stays_disabled(self):
        original=Path.unlink
        def fail(path,*args,**kwargs):
            if path==self.folder/'backup-launcher/old.txt':raise PermissionError('fixture locked')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',fail):
            with self.assertRaisesRegex(PermissionError,'locked'):self.remove()
        data=maintenance.journal(self.root)
        self.assertTrue(data['rollback_discarded']);self.assertEqual(data['backup_cleanup']['status'],'failed')
        self.assertEqual(data['backup_cleanup']['removed_files'],1);maintenance.require_idle(self.root)
        with self.assertRaisesRegex(ValueError,'已确认清理'):maintenance.recover(self.root,probe=lambda root:[])
        self.assertEqual(self.remove()['removed_files'],1)
    def test_unlisted_file_appearing_mid_removal_is_preserved_and_retry_refused(self):
        original=Path.unlink
        def add(path,*args,**kwargs):
            if path==self.folder/'backup-application/old.txt':(path.parent/'new-note').write_text('keep new note')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',add):
            with self.assertRaises(OSError):self.remove()
        with self.assertRaisesRegex(ValueError,'剩余备份已变化'):self.preview()
        self.assertEqual((self.folder/'backup-application/new-note').read_text(),'keep new note')
    def test_installed_source_or_moved_state_is_refused(self):
        (self.root/'pyproject.toml').write_text('source')
        with self.assertRaisesRegex(ValueError,'源码'):self.preview()
    def test_no_maintenance_has_nothing_to_remove(self):
        (self.root/maintenance.JOURNAL).unlink()
        self.assertEqual(self.preview()['inventory'],[]);self.assertEqual(self.remove()['removed_files'],0)
    def test_unrelated_state_change_preserves_backups(self):
        from installer.checkpoint import save_state
        state=read_state(self.root);state['plan']['model']='changed';state['plan_sha256']=fingerprint(state['plan']);save_state(self.root,state)
        with self.assertRaisesRegex(ValueError,'记录发生变化'):self.preview()
    def test_changed_backup_ancestor_is_not_followed(self):
        # Junction/reparse checks are covered by the shared _tree tests; reject via guard here too.
        original=cleanup.guard_path
        def refuse(path):
            if path==self.folder/'backup-application':raise ValueError('reparse fixture')
            return original(path)
        with patch.object(cleanup,'guard_path',refuse):
            with self.assertRaisesRegex(ValueError,'reparse'):self.preview()
        self.assertTrue((self.folder/'backup-application/old.txt').exists())


class CleanupUiTests(unittest.TestCase):
    def test_preview_and_separate_confirmation_are_required(self):
        import tkinter as tk
        from installer.maintenance_ui import Maintenance
        try:root=tk.Tk();root.withdraw()
        except tk.TclError as error:self.skipTest(str(error))
        calls=[];allow=[False]
        preview=dict(inventory=[{}],bytes_to_remove=512,file_count=1,plan_sha256='fixture')
        ui=Maintenance(root,Path('D:/test'),Path('D:/bundle'),Path('D:/setup.exe'),
            cleanup_preview=lambda *args:preview,cleanup=lambda *args:calls.append(args) or dict(removed_files=1,removed_bytes=512),confirm=lambda text:allow[0])
        def pump():
            deadline=time.monotonic()+3
            while ui.busy and time.monotonic()<deadline:root.update();time.sleep(.01)
            self.assertFalse(ui.busy)
        try:
            ui.cleanup();self.assertEqual(calls,[])
            ui.preview_cleanup();pump();ui.cleanup();self.assertEqual(calls,[])
            allow[0]=True;ui.cleanup();pump();self.assertEqual(len(calls),1)
        finally:
            if ui.thread:ui.thread.join(3)
            for callback in root.tk.call('after','info'):root.after_cancel(callback)
            root.destroy()


if __name__=='__main__':unittest.main()
