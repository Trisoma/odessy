import importlib.util
import hashlib
import io
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


def auditor():
    file=Path(__file__).parents[1]/'tool/audit_setup_artifact.py'
    spec=importlib.util.spec_from_file_location('setup_artifact_audit',file)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def _wheel(name='odessy', version='0.5.0.dev0'):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        archive.writestr(name+'-'+version+'.dist-info/METADATA',
                         f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nLicense-Expression: MIT\n')
    return buffer.getvalue()


def _release_payload(directory, *, desktop_entry='desktop/odessy_desktop.exe', bridge=True):
    """A minimal payload shaped like the shipping Flutter/NSIS artifact."""
    wheel=_wheel(); wheel_name='odessy-0.5.0.dev0-py3-none-any.whl'
    manifest=json.dumps({'schema_version':1,'artifacts':[
        {'file':wheel_name,'sha256':hashlib.sha256(wheel).hexdigest(),'license':'MIT'}]}).encode()
    files={'wheelhouse/'+wheel_name:wheel,'wheelhouse/manifest.json':manifest}
    if bridge: files['bootstrap/odessy/setup/bridge.py']=b'def run(): pass\n'
    if desktop_entry: files[desktop_entry]=b'MZ desktop'
    for name,content in files.items():
        path=directory/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
    inventory={name:hashlib.sha256(content).hexdigest() for name,content in files.items()}
    components=['python']+(['desktop'] if desktop_entry else [])
    (directory/'setup-context.json').write_text(json.dumps(
        {'schema_version':1,'components':components,'files':inventory}))
    output=directory.with_suffix('.zip')
    with zipfile.ZipFile(output,'w') as archive:
        archive.writestr('setup-context.json',(directory/'setup-context.json').read_bytes())
        for name,content in files.items(): archive.writestr(name,content)
    return output


def test_release_payload_audits_the_flutter_setup_entry(tmp_path):
    output=_release_payload(tmp_path/'payload')
    report=auditor().audit(output)
    assert report['setup_entry']=='desktop/odessy_desktop.exe'
    assert report['components']==['python','desktop']


def test_release_packaging_refuses_the_removed_tkinter_wizard(tmp_path):
    output=_release_payload(tmp_path/'payload')
    with zipfile.ZipFile(output,'a') as archive:
        archive.writestr('bootstrap/installer/wizard.py','# removed Tkinter wizard')
    with pytest.raises(ValueError,match='removed Tkinter/PyInstaller'):
        auditor().audit(output)


def test_release_payload_must_carry_the_setup_entry_and_bridge(tmp_path):
    with pytest.raises(ValueError,match='desktop component'):
        auditor().audit(_release_payload(tmp_path/'legacy-desktop',
                                         desktop_entry='desktop/OdessySetup.exe'))
    with pytest.raises(ValueError,match='setup bridge'):
        auditor().audit(_release_payload(tmp_path/'no-bridge',bridge=False))


def test_staged_harness_runtime_is_verified_and_packaged(tmp_path,monkeypatch):
    wheels=tmp_path/'wheels';wheels.mkdir();name='odessy-0.5.0-py3-none-any.whl';(wheels/name).write_bytes(b'wheel')
    licenses=tmp_path/'licenses.json';licenses.write_text(json.dumps({name:'Apache-2.0'}))
    harness=tmp_path/'harness-runtime';harness.mkdir()
    (harness/'engine.exe').write_bytes(b'MZ engine');(harness/'harness-runtime.json').write_text('{}')
    import installer.components.harness as declaration
    monkeypatch.setattr(declaration,'verify',lambda target:{'files_verified':1})
    output=tmp_path/'setup.zip';builder().build(wheels,licenses,output,harness=harness)
    with zipfile.ZipFile(output) as archive:
        manifest=json.loads(archive.read('setup-context.json'))
        assert manifest['components']==['python','harness']
        assert 'harness/engine.exe' in manifest['files']
        assert 'harness/harness-runtime.json' in manifest['files']
    (harness/'undeclared.bin').write_bytes(b'extra')
    with pytest.raises(ValueError,match='undeclared'):
        builder().build(wheels,licenses,tmp_path/'again.zip',harness=harness)


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


def test_tool_component_carries_the_execution_contract(tmp_path):
    """The Node tool server finds the shared contract by walking up from its module."""
    wheels=tmp_path/'wheels';wheels.mkdir();name='odessy-0.5.0-py3-none-any.whl';(wheels/name).write_bytes(b'wheel')
    licenses=tmp_path/'licenses.json';licenses.write_text(json.dumps({name:'Apache-2.0'}))
    tools=tmp_path/'tools';tools.mkdir();(tools/'main.js').write_bytes(b'console.log(1)')
    output=tmp_path/'setup.zip';builder().build(wheels,licenses,output,toolserver=tools)
    contract=(Path(__file__).parents[1]/'src/odessy/execution/contract.json').read_bytes()
    with zipfile.ZipFile(output) as archive:
        assert archive.read('tools/src/odessy/execution/contract.json')==contract
    # A tool bundle that already carries its own copy is not duplicated.
    (tools/'src/odessy/execution').mkdir(parents=True)
    (tools/'src/odessy/execution/contract.json').write_bytes(b'{"version":1}')
    again=tmp_path/'again.zip';builder().build(wheels,licenses,again,toolserver=tools)
    with zipfile.ZipFile(again) as archive:
        assert archive.read('tools/src/odessy/execution/contract.json')==b'{"version":1}'


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
        # The pinned voice manifest is data the helper reads by path; a payload
        # without it cannot offer voice installation at all.
        assert 'bootstrap/odessy/setup/voice-artifacts.json' in archive.namelist()
        assert 'bootstrap/odessy/setup/voice_install.py' in archive.namelist()
        assert not any('model' in name.lower() and name.endswith('.onnx') for name in archive.namelist())


def test_bundle_refuses_missing_license_and_existing_destination(tmp_path):
    wheels=tmp_path/'wheels';wheels.mkdir();(wheels/'odessy-0.5.0-py3-none-any.whl').write_bytes(b'wheel')
    licenses=tmp_path/'licenses.json';licenses.write_text('{}')
    with pytest.raises(ValueError): builder().build(wheels,licenses,tmp_path/'setup.zip')


def test_desktop_payload_requires_and_hashes_credential_runtime(tmp_path):
    wheels = tmp_path / 'wheels'; wheels.mkdir()
    app = 'odessy-0.5.0-py3-none-any.whl'; (wheels / app).write_bytes(b'app')
    desktop = tmp_path / 'desktop'; desktop.mkdir()
    licenses = tmp_path / 'licenses.json'; licenses.write_text(json.dumps({app: 'Apache-2.0'}))
    with pytest.raises(ValueError, match='keyring'):
        builder().build(wheels, licenses, tmp_path / 'missing.zip', desktop=desktop)
    keyring = 'keyring-25.7.0-py3-none-any.whl'; (wheels / keyring).write_bytes(b'credential-runtime')
    licenses.write_text(json.dumps({app: 'Apache-2.0', keyring: 'MIT'}))
    output = tmp_path / 'desktop.zip'
    builder().build(wheels, licenses, output, desktop=desktop)
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read('wheelhouse/manifest.json'))
        assert any(a['file'] == keyring and a['license'] == 'MIT' for a in manifest['artifacts'])
        assert 'bootstrap/odessy/credentials.py' in archive.namelist()


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


def test_extracted_bootstrap_can_describe_model_credentials(tmp_path):
    wheels=tmp_path/'wheels';wheels.mkdir()
    name='odessy-0.5.0-py3-none-any.whl';(wheels/name).write_bytes(b'wheel')
    licenses=tmp_path/'licenses.json';licenses.write_text(json.dumps({name:'Apache-2.0'}))
    output=tmp_path/'bootstrap.zip';builder().build(wheels,licenses,output)
    extracted=tmp_path/'extracted'
    with zipfile.ZipFile(output) as archive:archive.extractall(extracted)
    bootstrap='import sys;sys.path.insert(0,sys.argv[1]);from odessy.setup.bridge import run;raise SystemExit(run())'
    request={'id':'bootstrap-status','operation':'model-credential','selection':{},'parameters':{
        'action':'describe','provider':'deepseek','endpoint':'https://bootstrap-credential-qualification.invalid'}}
    result=subprocess.run([sys.executable,'-I','-c',bootstrap,str(extracted/'bootstrap')],input=json.dumps(request)+'\n',
                          cwd=tmp_path,text=True,capture_output=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['result']['source'] in ('env','none')

def _metadata(text):
    from email.parser import BytesParser
    return BytesParser().parsebytes(text.encode('utf-8'))


def test_license_is_not_called_unknown_when_metadata_uses_an_osi_classifier():
    """`backports.tarfile` and `jaraco.classes` publish only the MIT classifier.

    PyPI reports no SPDX expression for them, but the wheel ships the MIT LICENSE
    and the OSI classifier, so the audit must resolve MIT instead of flagging it.
    """
    derive = auditor().derive_license
    assert derive(_metadata('Metadata-Version: 2.1\nLicense-Expression: Apache-2.0\n\n')) == ('Apache-2.0', 'metadata-expression')
    assert derive(_metadata('Metadata-Version: 2.1\nClassifier: License :: OSI Approved :: MIT License\n\n')) == ('MIT', 'metadata-classifier')
    # An ambiguous classifier is reported verbatim, never guessed into one BSD variant.
    assert derive(_metadata('Metadata-Version: 2.1\nClassifier: License :: OSI Approved :: BSD License\n\n')) == ('BSD License', 'metadata-classifier')
    assert derive(_metadata('Metadata-Version: 2.1\n\n')) == (None, 'none')


def test_the_shipped_wheel_license_inventory_covers_every_pinned_runtime_wheel():
    import re
    root = Path(__file__).parents[1]
    pinned = json.loads((root/'installer/releases/windows-x64-py312/python-wheels-windows-x64.json').read_text())
    inventory = json.loads((root/'installer/releases/windows-x64-py312/wheel-licenses.json').read_text())
    def canonical(name): return re.sub(r'[-_.]+', '-', name).lower()
    rows = {canonical(row['name']): row for row in inventory['wheels']}
    assert len(rows) == len(inventory['wheels'])
    assert {canonical(a['id']) for a in pinned['artifacts']} <= set(rows)
    for row in inventory['wheels']:
        assert row['license'] and row['license_source'] in ('metadata-expression', 'metadata-classifier'), row
        assert re.fullmatch('[a-f0-9]{64}', row['sha256']), row
    # The two resolved gaps stay explained in the same repository-owned file.
    gaps = {entry['wheel']: entry for entry in inventory['resolved_gaps']}
    assert gaps['backports.tarfile-1.2.0-py3-none-any.whl']['license'] == 'MIT'
    assert gaps['jaraco.classes-3.4.0-py3-none-any.whl']['license'] == 'MIT'

def _real_wheel(directory, name='odessy', version='0.5.0.dev0', classifier_only=False):
    directory.mkdir(parents=True, exist_ok=True)
    filename = f'{name}-{version}-py3-none-any.whl'
    metadata = (f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n'
                + ('Classifier: License :: OSI Approved :: MIT License\n'
                   if classifier_only else 'License-Expression: MIT\n'))
    with zipfile.ZipFile(directory/filename, 'w') as archive:
        archive.writestr(f'{name}-{version}.dist-info/METADATA', metadata)
    return directory/filename


def test_a_build_derives_an_unreviewed_placeholder_from_the_wheel_and_refuses_a_contradiction(tmp_path):
    wheels = tmp_path/'wheels'
    wheel = _real_wheel(wheels, classifier_only=True)
    licenses = tmp_path/'licenses.json'
    licenses.write_text(json.dumps({wheel.name: 'UNREVIEWED-NO-METADATA'}))
    output = tmp_path/'derived.zip'
    builder().build(wheels, licenses, output)
    with zipfile.ZipFile(output) as archive:
        assert json.loads(archive.read('wheelhouse/manifest.json'))['artifacts'][0]['license'] == 'MIT'
    licenses.write_text(json.dumps({wheel.name: 'GPL-3.0-only'}))
    with pytest.raises(ValueError, match='contradicts its metadata'):
        builder().build(wheels, licenses, tmp_path/'contradiction.zip')

def test_monitor_and_link_payload_is_verified_and_role_selected(tmp_path):
    from odessy.setup.package_context import context, validate_components
    wheels=tmp_path/'wheels';wheels.mkdir()
    name='odessy-0.5.0-py3-none-any.whl';(wheels/name).write_bytes(b'wheel')
    licenses=tmp_path/'licenses.json';licenses.write_text(json.dumps({name:'Apache-2.0'}))
    monitor=tmp_path/'monitor';monitor.mkdir();(monitor/'odessy-monitor.exe').write_bytes(b'monitor')
    link=tmp_path/'link';link.mkdir();(link/'odessy-link.exe').write_bytes(b'link')
    output=tmp_path/'candidate.zip'
    builder().build(wheels,licenses,output,monitor=monitor,rust=link)
    extracted=tmp_path/'payload'
    with zipfile.ZipFile(output) as archive:
        archive.extractall(extracted)
        manifest=json.loads(archive.read('setup-context.json'))
    assert {'monitor','link'} <= set(context(payload=extracted,root=tmp_path/'app')['components'])
    # This fixture has no desktop: include that inventory solely for role selection.
    manifest['components'].remove('python')  # bootstrap fixture has no bundled runtime
    manifest['components'].append('desktop')
    manifest['files']['desktop/odessy_desktop.exe']='a'*64
    assert {'monitor','link'} <= set(validate_components(manifest,'coordinator')['copy'])
    execution=validate_components(manifest,'execution')['copy']
    assert 'link' in execution and 'monitor' not in execution
    (extracted/'monitor/odessy-monitor.exe').write_bytes(b'changed')
    with pytest.raises(ValueError,match='checksum mismatch'):
        context(payload=extracted,root=tmp_path/'app')
