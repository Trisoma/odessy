"""Runtime access reporting and Windows ACL inheritance regression checks."""
from __future__ import annotations
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from installer.access import check_runtime_access, is_elevated
from installer.runtime import prepare_runtime


class AccessTests(unittest.TestCase):
    def test_missing_file_and_denied_file_have_distinct_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=Path(folder)
            with self.assertRaisesRegex(ValueError,'缺失'): check_runtime_access(runtime)
            with patch.object(Path,'stat',side_effect=PermissionError('denied')):
                with self.assertRaisesRegex(ValueError,'权限|账户'): check_runtime_access(runtime)

    def test_both_executables_are_readable_before_reporting_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for child in ('python-runtime/python.exe','node-runtime/node.exe'):
                path=root/child; path.parent.mkdir(); path.write_bytes(b'fixture')
            check_runtime_access(root)
            with patch.object(Path,'open',side_effect=PermissionError('cannot read')):
                with self.assertRaisesRegex(ValueError,'账户'): check_runtime_access(root)

    @unittest.skipUnless(os.name=='nt','Windows ACL inheritance')
    def test_final_runtime_inherits_explicit_user_rule_from_install_parent(self):
        import hashlib, io, tarfile
        from installer.manifest import Artifact, Manifest
        with tempfile.TemporaryDirectory() as folder:
            parent=Path(folder)/'installation'; parent.mkdir()
            env={key:value for key,value in os.environ.items() if key.upper() != 'PSMODULEPATH'}
            env['ODESSY_ACL_TEST_PATH']=str(parent)
            grant='''$p=$env:ODESSY_ACL_TEST_PATH
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
$acl=Get-Acl -LiteralPath $p
$rule=New-Object Security.AccessControl.FileSystemAccessRule($sid,'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow')
$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $p -AclObject $acl
'''
            subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',grant],env=env,check=True,capture_output=True)
            archive=Path(folder)/'fixture.tar.gz'
            with tarfile.open(archive,'w:gz') as stream:
                entry=tarfile.TarInfo('python/python.exe'); entry.size=4; stream.addfile(entry,io.BytesIO(b'fake'))
            artifact=Artifact('python-runtime','3.12.13','https://example.test/python',archive.stat().st_size,hashlib.sha256(archive.read_bytes()).hexdigest())
            manifest=Manifest('fixture',(artifact,))
            resources={'runtimes':{'python-runtime':{'format':'tar.gz','prefix':'python','unpacked_bytes':4}}}
            target=parent/'runtime'
            prepare_runtime(manifest,resources,target,Path(folder)/'cache',download=lambda *a:SimpleNamespace(path=archive),probe=lambda *a:{'version':'3.12.13','bits':64})
            query='''$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$acl=Get-Acl -LiteralPath (Join-Path $env:ODESSY_ACL_TEST_PATH 'runtime')
$rules=$acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])
@($rules | Where-Object { $_.IdentityReference.Value -eq $sid -and $_.IsInherited }).Count
'''
            count=subprocess.check_output(['powershell','-NoProfile','-NonInteractive','-Command',query],env=env,text=True)
            self.assertGreater(int(count.strip()),0,'Runtime inherited private tempfile ACLs instead of the install parent')
            self.assertFalse((target/'.odessy-preparing').exists())


if __name__=='__main__': unittest.main()
