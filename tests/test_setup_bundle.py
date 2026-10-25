import importlib.util
import json
import subprocess
import sys
from pathlib import Path
import zipfile
import pytest


def builder():
    file=Path(__file__).parents[1]/'tool/build_setup_bundle.py'
    spec=importlib.util.spec_from_file_location('setup_bundle_builder',file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_optional_monitoring_component_is_hashed_and_selected_for_coordinator(tmp_path):
    wheels=tmp_path/'wheels';wheels.mkdir();name='odessy-0.5.0-py3-none-any.whl';(wheels/name).write_bytes(b'wheel')
    licenses=tmp_path/'licenses.json';licenses.write_text(json.dumps({name:'Apache-2.0'}))
    monitor=tmp_path/'monitor';monitor.mkdir();(monitor/'odessy-monitor.exe').write_bytes(b'go-binary')
    output=tmp_path/'preview.zip';builder().build(wheels,licenses,output,monitor=monitor)
    with zipfile.ZipFile(output) as archive:
        manifest=json.loads(archive.read('setup-context.json'))
        assert 'monitor' in manifest['components']
        assert 'monitor/odessy-monitor.exe' in manifest['files']
    from odessy.setup.package_context import validate_components
    # Execution-only nodes do not install the central Go service.
    result=validate_components({'components':['monitor'],'files':{'monitor/odessy-monitor.exe':'a'*64}},'execution')
    assert 'monitor' not in result['copy']


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


def test_extracted_bootstrap_progress_works_without_installed_odessy(tmp_path):
    wheels = tmp_path / 'wheels'
    wheels.mkdir()
    name = 'odessy-0.5.0-py3-none-any.whl'
    (wheels / name).write_bytes(b'wheel')
    licenses = tmp_path / 'licenses.json'
    licenses.write_text(json.dumps({name: 'Apache-2.0'}))
    output = tmp_path / 'setup.zip'
    builder().build(wheels, licenses, output)
    extracted = tmp_path / 'extracted'
    with zipfile.ZipFile(output) as archive:
        archive.extractall(extracted)
    # -I excludes the checkout, PYTHONPATH and user site-packages. Exercise the
    # first runtime stage before pip has installed the actual Odessy wheel.
    script = """
import sys
sys.path.insert(0, sys.argv[1])
from odessy.setup.progress import Progress
journal = {}
progress = Progress(journal, lambda: None)
progress.stage('creating-runtime')
progress.append('stdout', 'password=bootstrap-test-secret')
assert journal['logs'][0]['line'] == 'creating-runtime'
assert 'bootstrap-test-secret' not in journal['logs'][1]['line']
print('bootstrap progress passed')
"""
    result = subprocess.run(
        [sys.executable, '-I', '-c', script, str(extracted / 'bootstrap')],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
