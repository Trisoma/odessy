import importlib.util
from pathlib import Path
import zipfile
import pytest


def module():
    spec=importlib.util.spec_from_file_location('windows_nsis',Path(__file__).parents[1]/'tool/windows_nsis.py')
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def test_native_script_is_single_layer_and_per_user(tmp_path):
    script=module().script(tmp_path/'files',tmp_path/'Setup.exe',tmp_path/'icon.ico')
    assert 'RequestExecutionLevel user' in script
    assert 'Unicode true' in script
    assert 'CRCCheck force' in script
    assert 'Page instfiles' in script
    assert 'ODESSY_SETUP_PAYLOAD' in script
    assert '--setup' in script
    assert 'payload.zip' not in script and 'powershell' not in script.lower()
    assert 'RMDir /r' not in script


def test_windows_extraction_rejects_case_collisions_and_aliases():
    value=module()
    with pytest.raises(ValueError):
        value.validate_members([zipfile.ZipInfo('desktop/App.exe'),zipfile.ZipInfo('desktop/app.exe')])
    for name in ('desktop/app.exe.','../outside','desktop/CON','desktop/app:stream'):
        with pytest.raises(ValueError):value.validate_members([zipfile.ZipInfo(name)])
