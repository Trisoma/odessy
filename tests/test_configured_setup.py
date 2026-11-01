import json
from pathlib import Path
import pytest
from odessy.setup.bridge import execute
from odessy.setup.contracts import SetupSelection
from odessy.setup.planning import build_plan
from odessy.setup.configuration import export_selection, export_environment


def test_saved_legacy_voice_defaults_restore_without_rewriting(tmp_path, monkeypatch):
    import hashlib
    from odessy.setup import configured
    from odessy.setup.contracts import SetupSelection, public
    monkeypatch.setattr(configured, 'desktop_state_file', lambda: tmp_path / 'desktop.json')
    chosen = SetupSelection.from_dict({'role':'combined', 'model':{'provider':'ollama','name':'existing'}})
    legacy = public(chosen)
    for key in ('audio_backend','audio_input_device','audio_output_device','wake_model'):
        legacy['voice'].pop(key)
    raw = (json.dumps({'schema_version':1,'selection':legacy},indent=2)+'\n').encode()
    path = tmp_path / 'old-selection.json'
    path.write_bytes(raw)
    original_hash = hashlib.sha256(raw).hexdigest()
    configured.remember({'config':str(path),'format':'selection','selection':legacy,
        'sha256':original_hash,'workspace':str(tmp_path)})
    restored = configured.resume()
    assert restored['configured'] is True
    assert restored['selection']['voice']['audio_backend'] == 'auto'
    assert path.read_bytes() == raw and restored['sha256'] == original_hash
    # Old missing fields cannot authorize a newly selected nondefault device.
    changed = public(chosen)
    changed['voice']['audio_input_device'] = 'different microphone'
    with pytest.raises(ValueError, match='does not match'):
        configured.saved_spec(SetupSelection.from_dict(changed),path,'selection')
    # A changed export still fails the remembered hash, even if parseable.
    path.write_bytes(raw.replace(b'existing', b'changed'))
    with pytest.raises(ValueError):
        configured.resume()

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

def test_restart_rejects_configuration_changed_after_review(tmp_path,monkeypatch):
    from odessy.setup import configured,service_entry
    monkeypatch.setenv('ODESSY_API_TOKEN','test-only')
    selection={'role':'combined','model':{'provider':'ollama','name':'B'}}
    path=tmp_path/'selection.json'
    export_selection(build_plan(SetupSelection.from_dict(selection)),path)
    spec=execute(dict(operation='launch',selection=selection,output=str(path)))
    calls=[];monkeypatch.setattr(service_entry,'run',lambda *a,**kw:calls.append(kw))
    with pytest.raises(ValueError,match='changed after restart review'):
        configured.main(['--config',str(path),'--format','selection','--selection-json',json.dumps(selection),'--sha256',spec['sha256'],
            '--workspace',str(tmp_path/'work'),'--port','23456','--expected-fingerprint','0'*64])
    assert calls==[]


def test_recovery_of_directory_unblocks_editing_without_reading_it(tmp_path):
    result = execute(dict(operation='recover', selection={'role':'combined'}, output=str(tmp_path)))
    assert result['recovery'] == 'conflict'


def test_configured_server_authenticates_and_stops_without_inference(tmp_path,monkeypatch):
    import os, secrets, socket, subprocess, sys, time, urllib.request, urllib.error
    selection = {'role': 'combined', 'model': {'provider': 'ollama', 'name': 'existing', 'endpoint':'http://127.0.0.1:11434'}}
    path = tmp_path/'setup.json'
    export_selection(build_plan(SetupSelection.from_dict(selection)),path)
    spec=execute(dict(operation='launch',selection=selection,output=str(path)))
    token=secrets.token_urlsafe(32)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    from odessy.setup import configured
    monkeypatch.setattr(configured,'desktop_state_file',lambda:tmp_path/'desktop.json')
    bootstrap='import sys,runpy;sys.path[:0]='+repr(sys.path)+';from pathlib import Path;import odessy.setup.configured as c;c.desktop_state_file=lambda:Path('+repr(str(tmp_path/'desktop.json'))+');runpy.run_module("odessy.setup.configured",run_name="__main__")'
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
            def capabilities():
                with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/v1/capabilities',headers={'Authorization':'Bearer '+token}),timeout=2) as response:
                    return json.load(response)
            before=capabilities()
            assert before['configuration']['model']['name']=='existing'
            changed={'role':'combined','model':{'provider':'ollama','name':'saved-B'}}
            second=tmp_path/'second.json'
            export_selection(build_plan(SetupSelection.from_dict(changed)),second)
            execute(dict(operation='activate',selection=changed,output=str(second)))
            from odessy.setup.component_settings import dispatch
            saved=dispatch({'action':'load'})['configuration']
            after=capabilities()
            assert after['configuration']==before['configuration']
            assert saved['owner']==after['configuration']['owner']
            assert saved['fingerprints']['llm']!=after['configuration']['fingerprints']['llm']
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


def test_review_forks_changed_configuration_without_overwriting(tmp_path):
    old={'role':'combined','model':{'provider':'deepseek','name':'Deepseek V4.1 Flash'}}
    target=tmp_path/'setup.json'
    export_selection(build_plan(SetupSelection.from_dict(old)),target)
    original=target.read_bytes()
    changed={'role':'combined','model':{'provider':'deepseek','name':'deepseek-flash'}}
    reviewed=execute(dict(operation='review',selection=changed,output=str(target),parameters={'new_revision':True}))
    assert reviewed['output']!=str(target)
    new=Path(reviewed['output'])
    execute(dict(operation='save',selection=changed,output=str(new),confirm_plan=reviewed['plan_id']))
    assert target.read_bytes()==original
    spec=execute(dict(operation='launch',selection=changed,output=str(new)))
    assert spec['selection']['model']['name']=='deepseek-flash'
