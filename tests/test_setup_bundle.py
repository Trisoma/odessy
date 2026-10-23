import importlib.util
import json
from pathlib import Path
import zipfile
import pytest


def builder():
    file=Path(__file__).parents[1]/'tool/build_setup_bundle.py'
    spec=importlib.util.spec_from_file_location('setup_bundle_builder',file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_bundle_contains_offline_bootstrap_and_license_manifest(tmp_path):
    wheels=tmp_path/'wheels';wheels.mkdir();(wheels/'odessy-0.5.0-py3-none-any.whl').write_bytes(b'wheel')
    licenses=tmp_path/'licenses.json';licenses.write_text(json.dumps({'odessy-0.5.0-py3-none-any.whl':'Apache-2.0'}))
    output=tmp_path/'setup.zip'
    builder().build(wheels,licenses,output)
    with zipfile.ZipFile(output) as archive:
        assert 'install.py' in archive.namelist()
        manifest=json.loads(archive.read('wheelhouse/manifest.json'))
        assert manifest['artifacts'][0]['license']=='Apache-2.0'
        assert 'bootstrap/odessy/setup/maintenance.py' in archive.namelist()
        assert not any('model' in name.lower() and name.endswith('.onnx') for name in archive.namelist())


def test_bundle_refuses_missing_license_and_existing_destination(tmp_path):
    wheels=tmp_path/'wheels';wheels.mkdir();(wheels/'odessy-0.5.0-py3-none-any.whl').write_bytes(b'wheel')
    licenses=tmp_path/'licenses.json';licenses.write_text('{}')
    with pytest.raises(ValueError): builder().build(wheels,licenses,tmp_path/'setup.zip')


def test_standalone_bundle_includes_runtime_and_native_launcher(tmp_path):
    wheels=tmp_path/'wheels';wheels.mkdir();name='odessy-0.5.0-py3-none-any.whl';(wheels/name).write_bytes(b'wheel')
    licenses=tmp_path/'licenses.json';licenses.write_text(json.dumps({name:'Apache-2.0'}))
    runtime=tmp_path/'python';runtime.mkdir();(runtime/'python.exe').write_bytes(b'python');(runtime/'LICENSE.txt').write_text('Python license')
    output=tmp_path/'standalone.zip'
    builder().build(wheels,licenses,output,runtime=runtime)
    with zipfile.ZipFile(output) as archive:
        assert 'runtime/python.exe' in archive.namelist()
        assert 'install.cmd' in archive.namelist()
        assert 'runtime-manifest.json' in archive.namelist()
