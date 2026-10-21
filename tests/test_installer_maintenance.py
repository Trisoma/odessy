"""Staging, activation, rollback and interrupted-maintenance boundaries."""
import copy
import gc
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from installer import maintenance,start
from installer.checkpoint import Step,run_steps,read_state,fingerprint
from installer.shortcuts import digest


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'installed';self.bundle=self.base/'bundle';self.bundle.mkdir()
        self.exe=self.base/'Setup.exe';self.exe.write_bytes(b'MZ new launcher')
        self.old_plan=dict(install_dir=str(self.root),cache_dir=str(self.base/'cache'),endpoint='http://127.0.0.1:11435',timeout=180,model='fixture:1b')
        run_steps(self.root,self.old_plan,[Step('application',lambda:{},lambda:{}),Step('connection',lambda:{},lambda:{})])
        for name in ('application','launcher'):
            folder=self.root/name;folder.mkdir();(folder/'old.txt').write_text('original '+name)
        (self.root/'models').mkdir();(self.root/'models/model').write_text('keep model')
        (self.root/'config').mkdir();(self.root/'config/odessy.env').write_text('keep config')
        self.old_state=read_state(self.root);self.prepared=[]

    def planner(self,root,bundle,exe,**kwargs):
        maintenance.require_idle(root)
        inventory=[]
        for name in ('application','launcher'):inventory.extend(maintenance._tree(root,root/name))
        value=dict(install_dir=str(root),executable_sha256=digest(exe),inventory=inventory,
                   new_plan=dict(self.old_plan,release_sha256='new-release'),tool_archive_sha256='tool-digest',
                   launcher=dict(schema_version=1,install_dir=str(root),executable=str(root/'launcher/OdessyLauncher.exe'),sha256=digest(exe),links=[],link_sha256={}))
        value['plan_sha256']=fingerprint(value);return value

    def assemble(self,runtime,deps,bundle,target):
        self.prepared.append(target);target.mkdir();(target/'new.txt').write_text('new application')
        return dict(python='private-python')

    def verify(self,root,app,state,plan):
        self.assertTrue((root/'application/old.txt').is_file())
        self.assertEqual((app/'new.txt').read_text(),'new application')
        return dict(inference_verified=True,tool_round_trip_verified=True)

    def apply(self,**kwargs):
        preview=self.planner(self.root,self.bundle,self.exe)
        return maintenance.execute(self.root,self.bundle,self.exe,preview['plan_sha256'],probe=lambda root:[],
            planner=self.planner,assemble=self.assemble,verify=kwargs.pop('verify',self.verify),
            final_check=kwargs.pop('final_check',lambda app:None),**kwargs)

    def test_new_application_is_verified_before_old_components_move(self):
        result=self.apply();self.assertEqual(result['status'],'completed')
        self.assertTrue((self.root/'application/new.txt').exists());self.assertFalse((self.root/'application/old.txt').exists())
        backup=self.root/'.installer/updates'/result['id']
        self.assertEqual((backup/'backup-application/old.txt').read_text(),'original application')
        self.assertEqual((self.root/'launcher/OdessyLauncher.exe').read_bytes(),self.exe.read_bytes())
        self.assertEqual((self.root/'models/model').read_text(),'keep model')
        self.assertEqual((self.root/'config/odessy.env').read_text(),'keep config')
        self.assertEqual(read_state(self.root)['plan']['release_sha256'],'new-release')

    def test_failed_real_check_keeps_original_state_and_components(self):
        with self.assertRaisesRegex(ValueError,'检查未通过'):
            self.apply(verify=lambda *args:dict(inference_verified=False))
        self.assertEqual(read_state(self.root),self.old_state)
        self.assertTrue((self.root/'application/old.txt').exists());self.assertTrue((self.root/'launcher/old.txt').exists())
        self.assertEqual(maintenance.journal(self.root)['status'],'rolled_back')

    def test_post_activation_failure_restores_both_old_components(self):
        def fail(app):raise ValueError('fixture final failure')
        with self.assertRaisesRegex(ValueError,'fixture final'):self.apply(final_check=fail)
        self.assertTrue((self.root/'application/old.txt').exists());self.assertTrue((self.root/'launcher/old.txt').exists())
        current=read_state(self.root);original=copy.deepcopy(self.old_state)
        current.pop('updated_at');original.pop('updated_at');self.assertEqual(current,original)
        record=maintenance.journal(self.root);self.assertEqual(record['status'],'rolled_back')
        self.assertTrue(list((self.root/'.installer/updates'/record['id']).glob('rejected-application-*')))

    def test_second_component_switch_failure_restores_already_switched_application(self):
        original=Path.rename
        def fail(path,target):
            if path.name=='new-launcher':raise PermissionError('fixture launcher locked')
            return original(path,target)
        with patch.object(Path,'rename',fail):
            with self.assertRaisesRegex(PermissionError,'locked'):self.apply()
        self.assertEqual((self.root/'application/old.txt').read_text(),'original application')
        self.assertEqual((self.root/'launcher/old.txt').read_text(),'original launcher')
        self.assertEqual(maintenance.journal(self.root)['status'],'rolled_back')

    def test_manual_rollback_preserves_new_files_and_restores_original(self):
        self.apply();result=maintenance.recover(self.root,probe=lambda root:[])
        self.assertEqual(result['status'],'rolled_back');self.assertTrue((self.root/'application/old.txt').exists())
        with self.assertRaisesRegex(ValueError,'没有可回退'):maintenance.recover(self.root,probe=lambda root:[])

    def test_lost_backup_cannot_be_reported_as_successful_rollback(self):
        result=self.apply();folder=self.root/'.installer/updates'/result['id']
        (folder/'backup-application/old.txt').unlink();(folder/'backup-application').rmdir()
        with self.assertRaisesRegex(ValueError,'备份'):maintenance.recover(self.root,probe=lambda root:[])
        self.assertTrue((self.root/'application/new.txt').exists())
        self.assertEqual(maintenance.journal(self.root)['status'],'recovery_required')
        with self.assertRaisesRegex(ValueError,'维护'):start.inspect_installation(self.root)

    def test_interrupted_switch_can_be_recovered_from_rename_evidence(self):
        result=self.apply();result['status']='activating';maintenance._save(self.root,result)
        self.assertEqual(maintenance.recover(self.root,probe=lambda root:[])['status'],'rolled_back')
        self.assertTrue((self.root/'application/old.txt').exists())

    def test_changes_during_preparation_abort_before_switching(self):
        def verify(root,app,state,plan):
            (root/'application/user-note').write_text('user changed during preparation')
            return dict(inference_verified=True,tool_round_trip_verified=True)
        with self.assertRaisesRegex(ValueError,'原应用文件发生变化'):self.apply(verify=verify)
        self.assertEqual((self.root/'application/user-note').read_text(),'user changed during preparation')
        self.assertTrue((self.root/'application/old.txt').exists())

    def test_changed_source_executable_invalidates_preview(self):
        preview=self.planner(self.root,self.bundle,self.exe);self.exe.write_bytes(b'MZ changed')
        with self.assertRaisesRegex(ValueError,'变化'):
            maintenance.execute(self.root,self.bundle,self.exe,preview['plan_sha256'],planner=self.planner,probe=lambda root:[])
        self.assertEqual(self.prepared,[]);self.assertFalse((self.root/maintenance.JOURNAL).exists())

    def test_corrupt_journal_is_refused(self):
        (self.root/maintenance.JOURNAL).write_text('{}')
        with self.assertRaisesRegex(ValueError,'记录损坏'):maintenance.require_idle(self.root)

    def test_completed_maintenance_rejects_rollback_after_unrelated_state_change(self):
        self.apply()
        from installer.checkpoint import save_state
        state=read_state(self.root);state['plan']['endpoint']='http://127.0.0.1:12345';state['plan_sha256']=fingerprint(state['plan']);save_state(self.root,state)
        with self.assertRaisesRegex(ValueError,'记录.*变化'):maintenance.recover(self.root,probe=lambda root:[])
        self.assertTrue((self.root/'application/new.txt').exists())


class MaintenanceUiTests(unittest.TestCase):
    def test_preview_and_confirmation_are_required(self):
        self.addCleanup(gc.collect)  # Run Tk finalizers on this main thread.
        import tkinter as tk
        from installer.maintenance_ui import Maintenance
        try:root=tk.Tk();root.withdraw()
        except tk.TclError as error:self.skipTest(str(error))
        calls=[];allow=[False]
        preview=dict(application_action='repair',required_free_bytes=512,plan_sha256='fixture')
        ui=Maintenance(root,Path('D:/test'),Path('D:/bundle'),Path('D:/setup.exe'),preview=lambda *args:preview,
            apply=lambda *args:calls.append(args) or dict(status='completed'),confirm=lambda message:allow[0])
        def pump():
            deadline=time.monotonic()+3
            while ui.busy and time.monotonic()<deadline:root.update();time.sleep(.01)
            self.assertFalse(ui.busy)
        try:
            ui.apply();self.assertEqual(calls,[])
            ui.preview();pump();ui.apply();self.assertEqual(calls,[])
            allow[0]=True;ui.apply();pump();self.assertEqual(len(calls),1)
        finally:
            if ui.thread:ui.thread.join(3)
            for callback in root.tk.call('after','info'):root.after_cancel(callback)
            root.destroy()


if __name__=='__main__':unittest.main()
