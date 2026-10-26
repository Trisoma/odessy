import json
from pathlib import Path
import pytest
from odessy.setup.bridge import execute
from odessy.setup.contracts import SetupSelection
from odessy.setup.planning import build_plan
from odessy.setup.configuration import export_selection, export_environment

@pytest.mark.parametrize('format', ['selection', 'env'])
def test_saved_setup_launch_spec_is_bound_to_exact_file(tmp_path, format):
    selection = {'role': 'combined', 'model': {'provider': 'ollama', 'name': 'existing', 'endpoint': 'http://127.0.0.1:11434'}}
    plan = build_plan(SetupSelection.from_dict(selection))
    path = tmp_path / ('setup.json' if format == 'selection' else 'odessy.env')
    (export_selection if format == 'selection' else export_environment)(plan, path)
    request = dict(operation='launch', selection=selection, output=str(path), format=format)
    spec = execute(request)
    assert Path(spec['python']).is_file()
    assert spec['config'] == str(path)
    assert len(spec['sha256']) == 64
    path.write_text('changed')
    with pytest.raises(ValueError, match='match'):
        execute(request)

def test_launch_never_accepts_unconfigured_or_missing_export(tmp_path):
    with pytest.raises(ValueError):
        execute(dict(operation='launch', selection={'role':'combined'}, output=str(tmp_path/'missing')))


def test_recovery_of_directory_unblocks_editing_without_reading_it(tmp_path):
    result = execute(dict(operation='recover', selection={'role':'combined'}, output=str(tmp_path)))
    assert result['recovery'] == 'conflict'


def test_configured_server_authenticates_and_stops_without_inference(tmp_path):
    import os, secrets, socket, subprocess, sys, time, urllib.request, urllib.error
    selection = {'role': 'combined', 'model': {'provider': 'ollama', 'name': 'existing', 'endpoint':'http://127.0.0.1:11434'}}
    path = tmp_path/'setup.json'
    export_selection(build_plan(SetupSelection.from_dict(selection)),path)
    spec=execute(dict(operation='launch',selection=selection,output=str(path)))
    token=secrets.token_urlsafe(32)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    bootstrap='import sys,runpy;sys.path[:0]='+repr(sys.path)+';runpy.run_module("odessy.setup.configured",run_name="__main__")'
    env={**os.environ,'ODESSY_API_TOKEN':token}
    args=[sys.executable,'-c',bootstrap,'--config',str(path),'--format','selection','--selection-json',json.dumps(selection),'--sha256',spec['sha256'],'--workspace',str(tmp_path/'workspace'),'--port',str(port),'--exit-on-stdin-close']
    with (tmp_path/'stderr.log').open('w+') as log:
        child=subprocess.Popen(args,env=env,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=log)
        try:
            url=f'http://127.0.0.1:{port}/v1/profiles'
            for _ in range(100):
                try:
                    with urllib.request.urlopen(urllib.request.Request(url,headers={'Authorization':'Bearer '+token}),timeout=1) as response:
                        data=json.load(response)
                    break
                except (OSError,urllib.error.URLError):
                    if child.poll() is not None:
                        log.seek(0);pytest.fail(log.read())
                    time.sleep(.1)
            else: pytest.fail('coordinator did not become ready')
            assert 'ollama' in json.dumps(data) and 'fake' not in json.dumps(data)
            with pytest.raises(urllib.error.HTTPError) as failure:urllib.request.urlopen(url,timeout=2)
            assert failure.value.code in (401,403)
            child.stdin.close()
            assert child.wait(timeout=8)==0
        finally:
            if child.poll() is None:child.kill();child.wait()


def test_desktop_remembers_and_restores_only_matching_saved_configuration(tmp_path, monkeypatch):
    import odessy.setup.configured as configured
    monkeypatch.setattr(configured, 'desktop_state_file', lambda: tmp_path/'desktop.json')
    selection={'role':'combined','model':{'provider':'ollama','name':'existing','endpoint':'http://127.0.0.1:11434'}}
    path=tmp_path/'setup.json'
    export_selection(build_plan(SetupSelection.from_dict(selection)),path)
    spec=execute(dict(operation='activate',selection=selection,output=str(path)))
    restored=execute(dict(operation='resume',selection={'role':'combined'}))
    assert restored['selection']['model']['name']=='existing'
    assert restored['config']==str(path)
    path.write_text('changed')
    with pytest.raises(ValueError):execute(dict(operation='resume',selection={'role':'combined'}))


def test_voice_asset_discovery_finds_files_without_executing_or_downloading(tmp_path, monkeypatch):
    from odessy.setup.voice_assets import discover_voice_assets
    monkeypatch.setenv('HOME',str(tmp_path))
    monkeypatch.setattr(Path,'home',classmethod(lambda cls: tmp_path))
    paths={'whisper_bin':'whisper.cpp/build/bin/whisper-cli','whisper_model':'whisper.cpp/models/ggml-tiny.en.bin','kokoro_model':'odessy-voice-test/kokoro-v1.0.int8.onnx','kokoro_voices':'odessy-voice-test/voices-v1.0.bin'}
    for name,relative in paths.items():
        path=tmp_path/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
    result=discover_voice_assets({})
    for name,relative in paths.items():assert str(tmp_path/relative) in result['candidates'][name]
    assert not result['missing']


def test_resume_existing_environment_export_without_reentering_model(tmp_path, monkeypatch):
    import odessy.setup.configured as configured
    monkeypatch.setattr(configured, 'desktop_state_file', lambda: tmp_path/'absent-state.json')
    monkeypatch.setattr(Path,'home',classmethod(lambda cls: tmp_path))
    selection=SetupSelection.from_dict({'role':'combined','model':{'provider':'lfm','name':'q8-profile','endpoint':'http://127.0.0.1:11434'}})
    export_environment(build_plan(selection),tmp_path/'odessy-runtime.env')
    restored=configured.resume()
    assert restored['selection']['model']['name']=='q8-profile'
    assert restored['format']=='env'
