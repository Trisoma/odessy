from pathlib import Path
import pytest
from odessy.setup.contracts import SetupSelection, ModelSelection, VoiceSelection


def test_probe_requires_explicit_consent_before_provider_construction(monkeypatch):
    from odessy.setup import diagnostics
    called=[]
    monkeypatch.setattr(diagnostics,'test_model',lambda *a:called.append(a))
    selection=SetupSelection(role='combined',model=ModelSelection(provider='ollama',name='test'))
    with pytest.raises(ValueError,match='consent'):
        diagnostics.probe(selection,'llm',run=True,consent=False)
    assert called==[]


def test_observation_is_independent_and_never_imports_custom_provider(monkeypatch):
    from odessy.setup import diagnostics
    selection=SetupSelection(role='combined',voice=VoiceSelection(tts_provider='missing_plugin:factory'))
    result=diagnostics.probe(selection,'tts')
    assert result['status']=='configured_unverified'
    assert result['inference_performed'] is False
    assert result['section']=='tts'


def test_cloud_diagnostic_disables_tools_and_never_repairs(monkeypatch):
    from odessy.models.cloud_model import CloudModel, _ParsedReply
    model=CloudModel('openai','test','not-a-real-key')
    sent=[]
    def exchange(field):
        sent.append((model._final_call,list(model._messages)))
        return _ParsedReply((), 'Hello')
    monkeypatch.setattr(model,'_exchange',exchange)
    assert model.test_connection()=='Hello'
    assert len(sent)==1 and sent[0][0] is True
    assert len(sent[0][1])==1


def test_cloud_diagnostic_rejects_tool_response_without_executing(monkeypatch):
    from odessy.models.cloud_model import CloudModel, _ParsedReply
    model=CloudModel('openai','test','not-a-real-key')
    monkeypatch.setattr(model,'_exchange',lambda _: _ParsedReply((object(),),'ignored'))
    with pytest.raises(ValueError,match='text'):
        model.test_connection()


def test_stt_file_test_needs_no_llm_and_removes_no_input(tmp_path,monkeypatch):
    import wave
    from odessy.setup import diagnostics
    from odessy.voice.stt import STTResult
    audio=tmp_path/'sample.wav'
    with wave.open(str(audio),'wb') as wav:
        wav.setparams((1,2,16000,0,'NONE','not compressed'));wav.writeframes(bytes(3200))
    closed=[]
    class Fake:
        def transcribe(self,path):assert path==audio;return STTResult('hello','ok')
        def close(self):closed.append(True)
    monkeypatch.setattr(diagnostics,'create_stt',lambda _:Fake())
    result=diagnostics.probe(SetupSelection(role='combined'),'stt',run=True,consent=True,audio=str(audio))
    assert result['text']=='hello' and closed==[True] and audio.exists()

def test_microphone_probe_without_model_is_bounded_and_closes_source(monkeypatch):
    from odessy.setup import diagnostics
    from odessy.voice import native_audio
    from odessy.voice.stt import STTResult
    reads=[];closed=[]
    class Source:
        def start(self):pass
        def read(self,size):reads.append(size);return bytes(size)
        def close(self):closed.append(True)
    class Provider:
        def transcribe(self,path):
            with diagnostics.wave.open(str(path)) as wav:assert wav.getnframes()==80000
            return STTResult('sample','ok')
    monkeypatch.setattr(native_audio,'recorder',lambda _:Source())
    monkeypatch.setattr(diagnostics,'create_stt',lambda _:Provider())
    result=diagnostics.probe(SetupSelection(role='combined'),'stt',run=True,consent=True)
    assert result['text']=='sample' and sum(reads)==160000 and closed

def test_cancelled_diagnostic_never_constructs_provider(monkeypatch):
    from odessy.setup import diagnostics
    called=[]
    monkeypatch.setattr(diagnostics,'test_model',lambda _:called.append(True))
    with pytest.raises(ValueError,match='cancelled'):
        diagnostics.probe(SetupSelection(role='combined'),'llm',run=True,consent=True,cancelled=lambda:True)
    assert called==[]

def test_cloud_probe_wire_has_no_tools_and_records_usage():
    from odessy.models.cloud_model import CloudModel
    sent=[]
    class Model(CloudModel):
        def _post_chat(self,payload):
            sent.append(payload)
            return {'choices':[{'message':{'role':'assistant','content':'Hello'}}],
                    'usage':{'prompt_tokens':12,'completion_tokens':2}}
    model=Model(provider='openai',model='test',api_key='fake')
    assert model.test_connection()=='Hello'
    assert len(sent)==1 and 'tools' not in sent[0] and 'tool_choice' not in sent[0]
    assert model.ledger.total.calls==1 and model.ledger.total.tokens==14

def test_real_stdio_helper_tests_loopback_model_without_coordinator(tmp_path):
    import json,os,subprocess,sys,threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    requests=[]
    class Server(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200);self.send_header('Content-Type','application/x-ndjson');self.end_headers()
            self.wfile.write(b'{"message":{"role":"assistant","content":"hello"},"done":true}\n')
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Server)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        source=Path(__file__).resolve().parents[1]/'src'
        request={'id':'probe-real','operation':'component-probe','selection':{'role':'combined',
                 'model':{'provider':'ollama','name':'fixture','endpoint':f'http://127.0.0.1:{server.server_port}'}},
                 'parameters':{'section':'llm','run':True,'consent':True}}
        process=subprocess.run([sys.executable,'-c',f'import sys;sys.path.insert(0,{str(source)!r});from odessy.setup.bridge import run;raise SystemExit(run())'],
            input=json.dumps(request)+'\n',text=True,capture_output=True,timeout=25,cwd=tmp_path)
        assert process.returncode==0,process.stderr+process.stdout
        result=json.loads(process.stdout)['result']
        assert result['status']=='tested' and result['model']=='fixture' and result['text']=='hello'
        assert len(requests)==1 and 'tools' not in requests[0]
    finally:server.shutdown();server.server_close();worker.join(2)

@pytest.mark.parametrize('section',['stt','tts'])
def test_cancellation_during_provider_creation_still_closes_it(section,tmp_path,monkeypatch):
    import wave
    from odessy.setup import diagnostics
    cancelled=False;closed=[]
    class Provider:
        def cancel(self):pass
        def close(self):closed.append(True)
    def factory(config):
        nonlocal cancelled
        cancelled=True
        import time
        time.sleep(.1)  # Watchdog observes cancellation before construction returns.
        return Provider()
    monkeypatch.setattr(diagnostics,'create_'+section,factory)
    path=tmp_path/'sample.wav'
    with wave.open(str(path),'wb') as wav:wav.setparams((1,2,16000,0,'NONE',''));wav.writeframes(bytes(3200))
    with pytest.raises(ValueError,match='cancelled'):
        diagnostics.probe(SetupSelection(role='combined'),section,run=True,consent=True,
                          audio=str(path),cancelled=lambda:cancelled)
    assert closed==[True]

def test_diagnostic_redacts_opaque_referenced_credential(monkeypatch):
    from odessy.setup import diagnostics
    secret='opaque-unpatterned-credential'
    monkeypatch.setenv('MY_MODEL_KEY',secret)
    monkeypatch.setattr(diagnostics,'test_model',lambda _: {'text':'echo '+secret,'usage':None})
    chosen=SetupSelection(role='combined',model=ModelSelection(provider='openai',name='fixture',credential_env='MY_MODEL_KEY'))
    result=diagnostics.probe(chosen,'llm',run=True,consent=True)
    assert secret not in result['text'] and '<redacted>' in result['text']

def test_missing_credential_has_safe_machine_readable_reason(monkeypatch):
    import io,json
    from odessy.setup.bridge import run
    monkeypatch.delenv('MY_MODEL_KEY',raising=False);monkeypatch.delenv('ODESSY_CLOUD_API_KEY',raising=False)
    request={'id':'credential-test','operation':'component-probe','selection':{'role':'combined',
        'model':{'provider':'openai','name':'fixture','credential_env':'MY_MODEL_KEY'}},
        'parameters':{'section':'llm','run':True,'consent':True}}
    output=io.BytesIO();run(io.BytesIO((json.dumps(request)+'\n').encode()),output)
    result=json.loads(output.getvalue())
    assert result['status']=='error' and result['code']=='credentials'
    assert 'MY_MODEL_KEY' in result['reason']

def test_cancel_before_diagnostic_playback_cannot_adopt_a_new_generation(monkeypatch):
    from odessy.setup import diagnostics
    from odessy.voice import native_audio
    from odessy.voice.tts import TTSResult
    cancelled=False;played=[]
    class Provider:
        def synthesize(self,text,path):
            with diagnostics.wave.open(str(path),'wb') as wav:
                wav.setparams((1,2,16000,0,'NONE',''));wav.writeframes(bytes(3200))
            return TTSResult(path,'ok')
    class Output:
        generation=0
        def stop(self):self.generation+=1
        def play(self,path,*,generation=None):
            nonlocal cancelled
            cancelled=True;self.stop()  # Stop lands just before playback admission.
            if generation is None or generation==self.generation:played.append(True)
    monkeypatch.setattr(diagnostics,'create_tts',lambda _:Provider())
    monkeypatch.setattr(native_audio,'player',lambda _:Output())
    with pytest.raises(ValueError,match='cancelled'):
        diagnostics.probe(SetupSelection(role='combined'),'tts',run=True,consent=True,cancelled=lambda:cancelled)
    assert played==[]
