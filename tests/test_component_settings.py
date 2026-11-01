import json
import pytest
from odessy.setup.bridge import execute
from odessy.setup import configured

def test_bridge_field_errors_are_additive_and_paths_follow_selected_provider(state):
    import io
    from odessy.setup.bridge import run
    initial=call()
    request={'id':'fields','operation':'component-settings','parameters':{'action':'review','section':'tts',
        'patch':{'tts_provider':'kokoro','kokoro_model':str(state/'missing.onnx')},'revision':initial['revision']}}
    output=io.BytesIO()
    assert run(io.BytesIO((json.dumps(request)+'\n').encode()),output)==2
    reply=json.loads(output.getvalue())
    assert 'kokoro_model' in reply['field_errors']
    assert reply['code']=='validation'
    # Inactive builtin paths are retained, not tested/executed for a custom provider.
    custom=call('review',section='tts',patch={'tts_provider':'custom_audio:factory','kokoro_model':str(state/'old-missing.onnx')},revision=initial['revision'])
    assert custom['selection']['voice']['kokoro_model'].endswith('old-missing.onnx')

@pytest.fixture
def state(tmp_path,monkeypatch):
    monkeypatch.setattr(configured,'desktop_state_file',lambda:tmp_path/'desktop.json')
    monkeypatch.setattr(configured.Path,'home',lambda:tmp_path)
    return tmp_path

def call(action='load',**kwargs):
    return execute({'operation':'component-settings','parameters':{'action':action,**kwargs}})

def test_tts_only_persists_and_does_not_require_llm_or_stt(state):
    initial=call()
    change={'tts_provider':'kokoro','kokoro_model':str(state/'kokoro.onnx'),'kokoro_voices':str(state/'voices.bin')}
    (state/'kokoro.onnx').write_bytes(b'fixture');(state/'voices.bin').write_bytes(b'fixture')
    reviewed=call('review',section='tts',patch=change,revision=initial['revision'])
    saved=call('save',section='tts',patch=change,revision=initial['revision'],confirm=reviewed['plan_id'])
    reopened=call()
    assert saved['restart_required'] is True
    assert reopened['selection']['voice']['kokoro_model']==change['kokoro_model']
    assert reopened['selection']['model'] is None
    assert reopened['selection']['voice']['whisper_bin']==''
    assert reopened['selection']['voice']['enabled'] is False
    assert configured.resume()['configured'] is False

def test_section_cannot_modify_other_component_and_stale_revision_fails(state):
    initial=call()
    with pytest.raises(ValueError):call('review',section='tts',patch={'stt_provider':'bad'},revision=initial['revision'])
    patch={'tts_provider':'custom.module:factory'}
    review=call('review',section='tts',patch=patch,revision=initial['revision'])
    call('save',section='tts',patch=patch,revision=initial['revision'],confirm=review['plan_id'])
    with pytest.raises(ValueError,match='changed'):call('review',section='tts',patch=patch,revision=initial['revision'])

def test_invalid_provider_does_not_write(state):
    initial=call()
    with pytest.raises(ValueError):call('review',section='tts',patch={'tts_provider':'not a provider'},revision=initial['revision'])
    assert not (state/'desktop.json').exists()

def test_installed_tts_patch_reuses_engine_and_preserves_llm(tmp_path):
    from test_setup_installed import make_installation
    root=tmp_path/'App';make_installation(root,model=True)
    initial=call(root=str(root))
    patch={'tts_provider':'custom_audio:factory'}
    review=call('review',root=str(root),section='tts',patch=patch,revision=initial['revision'])
    call('save',root=str(root),section='tts',patch=patch,revision=initial['revision'],confirm=review['plan_id'])
    reopened=call(root=str(root))
    assert reopened['selection']['model']==initial['selection']['model']
    assert reopened['selection']['voice']['stt_provider']=='whisper.cpp'
    assert reopened['selection']['voice']['tts_provider']=='custom_audio:factory'


def test_source_persistence_survives_separate_helper_processes(tmp_path):
    import subprocess,sys
    state_path=tmp_path/'desktop.json'
    bootstrap='import sys;sys.path[:0]='+repr(sys.path)+';from pathlib import Path;from odessy.setup import configured;configured.desktop_state_file=lambda:Path(sys.argv[1]);configured.Path.home=lambda:Path(sys.argv[1]).parent;from odessy.setup.bridge import run;run()'
    def request(action,**values):
        data={'id':'separate','operation':'component-settings','parameters':{'action':action,**values}}
        completed=subprocess.run([sys.executable,'-c',bootstrap,str(state_path)],input=(json.dumps(data)+'\n').encode(),capture_output=True,timeout=10)
        assert completed.returncode==0,completed.stderr.decode()
        reply=json.loads(completed.stdout)
        assert reply['status']=='ok',reply
        return reply['result']
    loaded=request('load');patch={'tts_provider':'custom_audio:factory'}
    reviewed=request('review',section='tts',patch=patch,revision=loaded['revision'])
    request('save',section='tts',patch=patch,revision=loaded['revision'],confirm=reviewed['plan_id'])
    assert request('load')['selection']['voice']['tts_provider']=='custom_audio:factory'
