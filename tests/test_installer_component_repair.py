"""Component repair selection, fixed-path rebuilding and transaction recovery."""
import copy
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch
from installer import component_repair as repair, maintenance
from installer.checkpoint import fingerprint,read_state
import test_installer_maintenance as fixtures


class ComponentTransactions(unittest.TestCase):
    assemble=fixtures.MaintenanceTests.assemble
    verify=fixtures.MaintenanceTests.verify
    apply=fixtures.MaintenanceTests.apply
    def setUp(self):
        fixtures.MaintenanceTests.setUp(self)
        self.blob='models/blobs/sha256-'+'a'*64
        self.names=['runtime','dependencies','ollama-runtime',self.blob]
        for name in self.names:
            path=self.root/name
            path.parent.mkdir(parents=True,exist_ok=True)
            if name.startswith('models/'):path.write_bytes(b'original model')
            else:path.mkdir();(path/'original').write_bytes(b'original component')

    def planner(self,*args,**kwargs):
        value=fixtures.MaintenanceTests.planner(self,*args,**kwargs)
        value['repair_components']=self.names
        value['base_inventory']=sum((maintenance._tree(self.root,self.root/name) for name in self.names),[])
        value.pop('plan_sha256');value['plan_sha256']=fingerprint(value)
        return value

    def replace(self,root,folder,preview,data):
        data['status']='repairing';maintenance._save(root,data)
        for name in self.names:
            target=root/name;backup=folder/('backup-'+name)
            backup.parent.mkdir(parents=True,exist_ok=True);target.rename(backup)
            if name.startswith('models/'):target.write_bytes(b'new model')
            else:target.mkdir();(target/'new').write_bytes(b'new component')

    def assert_original(self):
        for name in self.names:
            target=self.root/name
            if name.startswith('models/'):self.assertEqual(target.read_bytes(),b'original model')
            else:self.assertEqual((target/'original').read_bytes(),b'original component')
        self.assertEqual((self.root/'config/odessy.env').read_text(),'keep config')

    def test_failed_verification_restores_all_base_components(self):
        with self.assertRaisesRegex(ValueError,'检查未通过'):
            self.apply(repair=self.replace,verify=lambda *args:{})
        self.assert_original()
        current=read_state(self.root);original=copy.deepcopy(self.old_state)
        current.pop('updated_at');original.pop('updated_at');self.assertEqual(current,original)
        self.assertEqual(maintenance.journal(self.root)['status'],'rolled_back')

    def test_post_activation_failure_restores_base_and_application(self):
        def fail(*args):raise ValueError('post activation')
        with self.assertRaisesRegex(ValueError,'post activation'):
            self.apply(repair=self.replace,final_check=fail)
        self.assert_original();self.assertTrue((self.root/'application/old.txt').exists())

    def test_successful_base_repair_can_be_manually_rolled_back(self):
        self.apply(repair=self.replace)
        self.assertEqual((self.root/self.blob).read_bytes(),b'new model')
        maintenance.recover(self.root,probe=lambda root:[]);self.assert_original()

    def test_interrupted_partial_component_repair_restores_originals(self):
        def interrupt(root,folder,preview,data):
            data['status']='repairing';maintenance._save(root,data)
            (root/'runtime').rename(folder/'backup-runtime')
            (root/'runtime').mkdir();(root/'runtime/partial').write_text('partial')
            raise OSError('interrupted rebuild')
        with self.assertRaisesRegex(OSError,'interrupted'):self.apply(repair=interrupt)
        self.assert_original()

    def test_failed_repair_of_missing_model_file_restores_absence(self):
        (self.root/self.blob).unlink()
        def create_missing(root,folder,preview,data):
            data['status']='repairing';maintenance._save(root,data)
            (root/self.blob).write_bytes(b'new file')
        with self.assertRaises(ValueError):self.apply(repair=create_missing,verify=lambda *args:{})
        self.assertFalse((self.root/self.blob).exists())

    def test_download_failure_leaves_original_components_in_place(self):
        def fail(*args):raise OSError('network failed')
        with patch.object(repair,'_artifacts',return_value=[object()]):
            # Use a real artifact so the progress reporter can name it.
            from types import SimpleNamespace
            with patch.object(repair,'_artifacts',return_value=[SimpleNamespace(id='fixture')]):
                with self.assertRaisesRegex(OSError,'network failed'):
                    self.apply(repair=lambda *args:repair.repair(*args,download=fail))
        self.assert_original();self.assertEqual(maintenance.journal(self.root)['status'],'rolled_back')

    def test_changed_component_during_download_is_preserved(self):
        def changed(artifact,cache):
            (self.root/'runtime/user-note').write_text('keep user change')
        from types import SimpleNamespace
        with patch.object(repair,'_artifacts',return_value=[SimpleNamespace(id='fixture')]):
            with self.assertRaisesRegex(ValueError,'发生变化'):
                self.apply(repair=lambda *args:repair.repair(*args,download=changed))
        self.assertEqual((self.root/'runtime/user-note').read_text(),'keep user change')
        self.assert_original()


class ComponentSelection(unittest.TestCase):
    def test_timed_out_probes_select_the_component_for_repair(self):
        for stage in ('runtime','dependencies','ollama_runtime','stdlib','integrity'):
            with self.subTest(stage=stage):
                steps=[SimpleNamespace(name=name,validate=lambda:{}) for name in ('runtime','dependencies','ollama_runtime','configuration')]
                timeout=subprocess.TimeoutExpired('fixture probe',30)
                if stage in ('runtime','dependencies','ollama_runtime'):
                    next(step for step in steps if step.name==stage).validate=Mock(side_effect=timeout)
                with patch.object(repair.workflow,'steps_for',return_value=steps), \
                     patch.object(repair,'run_step',side_effect=timeout if stage=='stdlib' else None), \
                     patch.object(repair,'dependency_integrity',side_effect=timeout if stage=='integrity' else None), \
                     patch.object(repair,'model_targets',return_value=[]):
                    names,issues=repair._health(Path(tempfile.gettempdir())/'installed',{})
                component={'stdlib':'runtime','integrity':'dependencies','ollama_runtime':'ollama-runtime'}.get(stage,stage)
                self.assertIn(component,names);self.assertIn('timed out',issues[component])
                if component=='runtime':self.assertIn('dependencies',names)

    def test_repairs_only_damaged_blob_and_keeps_old_bytes_for_rollback(self):
        import hashlib,tempfile
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as scratch:
            base=Path(scratch);root=base/'installed';root.mkdir();(root/'.installer').mkdir()
            folder=root/'.installer/updates/fixture';folder.mkdir(parents=True)
            content=b'verified model layer';source=base/'source';source.write_bytes(content)
            artifact=SimpleNamespace(id='model-blob-0',sha256=hashlib.sha256(content).hexdigest(),size_bytes=len(content))
            target=repair.model.blob_path(root/'models',artifact);target.parent.mkdir(parents=True);target.write_bytes(b'broken')
            other=target.parent/('sha256-'+'b'*64);other.write_bytes(b'healthy other model')
            name=target.relative_to(root).as_posix()
            preview=dict(install_dir=str(root),new_plan=dict(cache_dir=str(base/'cache')),repair_components=[name],base_inventory=maintenance._tree(root,target))
            downloads=[]
            def fetch(a,c):downloads.append(a.id);return SimpleNamespace(path=source)
            def validate(path):
                self.assertEqual(target.read_bytes(),content);return {'verified':True}
            with patch.object(repair.model,'load_release',return_value=(SimpleNamespace(artifacts=[artifact]),{'model':'fixture:1b'},b'raw manifest')),patch.object(repair.model,'verify_model',side_effect=validate):
                repair.repair(root,folder,preview,{},download=fetch)
            self.assertEqual((folder/('backup-'+name)).read_bytes(),b'broken')
            self.assertEqual(other.read_bytes(),b'healthy other model');self.assertEqual(downloads,['model-blob-0','model-blob-0'])

    def test_raw_record_detects_a_missing_file_and_changed_bytes(self):
        import base64,hashlib,json,subprocess,sys,tempfile
        with tempfile.TemporaryDirectory() as scratch:
            root=Path(scratch);info=root/'repair_audit_fixture-1.0.dist-info';info.mkdir()
            (info/'METADATA').write_text('Name: repair-audit-fixture\nVersion: 1.0\n')
            expected=base64.urlsafe_b64encode(hashlib.sha256(b'healthy').digest()).rstrip(b'=').decode()
            (info/'RECORD').write_text('payload.py,sha256='+expected+',7\n')
            code='import sys;sys.prefix=sys.argv[2];sys.path.insert(0,sys.argv[2]);exec('+repr(repair.DEPENDENCY_CHECK)+')'
            def audit():
                return json.loads(subprocess.check_output([sys.executable,'-I','-B','-c',code,json.dumps(['repair-audit-fixture']),str(root)],text=True))
            self.assertTrue(audit()['bad'])
            (root/'payload.py').write_bytes(b'changed');self.assertTrue(audit()['bad'])
            (root/'payload.py').write_bytes(b'healthy');self.assertFalse(audit()['bad'])

    def test_rollback_paths_reject_traversal_and_unexpected_model_files(self):
        for name in ('../outside','models/custom','models/blobs/../config',{},'models/blobs/sha256-'+'a'*63):
            with self.subTest(name=name),self.assertRaises(ValueError):maintenance._components({'components':[name]})

    def test_only_selected_model_blob_is_downloaded(self):
        manifest,_,_=repair.model.load_release()
        root=Path('D:/fixture')
        selected=manifest.artifacts[1]
        name=repair.model.blob_path(root/'models',selected).relative_to(root).as_posix()
        values=repair._artifacts(dict(install_dir=str(root),repair_components=[name]))
        self.assertEqual([a.id for a in values],[selected.id])

    def test_runtime_rebuild_also_rebuilds_fixed_path_venv(self):
        from types import SimpleNamespace
        steps=[SimpleNamespace(name=name,validate=lambda:{}) for name in ('runtime','dependencies','ollama_runtime','configuration')]
        steps[0].validate=lambda:None
        with patch.object(repair.workflow,'steps_for',return_value=steps),patch.object(repair,'dependency_integrity'),patch.object(repair,'model_targets',return_value=[]):
            names,issues=repair._health(Path('D:/fixture'),{})
        self.assertEqual(names,['runtime','dependencies'])

    def test_missing_configuration_is_not_silently_rewritten(self):
        from types import SimpleNamespace
        steps=[SimpleNamespace(name=name,validate=lambda:{}) for name in ('runtime','dependencies','ollama_runtime')]
        steps.append(SimpleNamespace(name='configuration',validate=lambda:None))
        with patch.object(repair.workflow,'steps_for',return_value=steps),patch.object(repair,'dependency_integrity'),patch.object(repair,'run_step'):
            with self.assertRaisesRegex(ValueError,'配置缺失'):repair._health(Path('D:/fixture'),{})


class RepairPlanConfirmation(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.gettempdir())/'repair-plan-fixture'
        self.free=10**12
        self.inventory=[]
        def base_plan(*args,**kwargs):
            return dict(install_dir=str(self.root),new_plan=dict(cache_dir=str(self.root.parent/'cache')),
                        required_free_bytes=1024,inventory=list(self.inventory),plan_sha256='base')
        for mocked in (patch.object(repair.maintenance,'plan',side_effect=base_plan),
                       patch.object(repair,'_health',return_value=([],{})),
                       patch.object(repair,'_artifacts',return_value=[]),
                       patch.object(repair,'volume_for',side_effect=lambda path:SimpleNamespace(id='disk',free_bytes=self.free))):
            mocked.start();self.addCleanup(mocked.stop)

    def preview(self):return repair.plan(self.root,self.root,self.root)

    def test_free_space_changes_do_not_invalidate_confirmation(self):
        before=self.preview();self.free-=4096;after=self.preview()
        self.assertNotEqual(before['space_budget'],after['space_budget'])
        self.assertEqual(before['plan_sha256'],after['plan_sha256'])

    def test_insufficient_space_still_stops_a_previously_confirmed_plan(self):
        self.preview();self.free=1
        with self.assertRaisesRegex(ValueError,'空间不足'):self.preview()

    def test_changed_files_still_invalidate_confirmation(self):
        before=self.preview();self.inventory.append(dict(path='application/changed.py',size=7))
        self.assertNotEqual(before['plan_sha256'],self.preview()['plan_sha256'])


if __name__=='__main__':unittest.main()
