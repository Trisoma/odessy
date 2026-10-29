"""Explicit, isolated provider probes. Never construct an agent or execute tools.

Each native helper is a separate diagnostic run with the existing cloud ledger,
budget and egress recorder. It does not spend from or reset a coordinator ledger.
"""
from dataclasses import asdict
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import time
import threading
import wave
from contextlib import contextmanager

from .contracts import SetupSelection
from .verification import KEY_ENV, credential_value
from odessy.voice.config import VoiceConfig
from odessy.voice.providers import create_stt, create_tts, close_providers, cancel_provider

class DiagnosticInputError(ValueError):
    """Only locally authored validation messages may cross the helper boundary."""
    def __init__(self,message,code='validation'):
        super().__init__(message);self.code=code

class ProviderTestError(RuntimeError):
    code='unavailable'

class ProbeCancellation:
    def __init__(self,cancelled):
        self.cancelled=cancelled;self.done=threading.Event();self.stopped=threading.Event()
        self.hooks=[];self.lock=threading.Lock();self.deadline=time.monotonic()+25
    def __enter__(self):
        def watch():
            while not self.done.wait(.05):
                if self.cancelled() or time.monotonic()>self.deadline:
                    with self.lock:self.stopped.set();hooks=list(self.hooks)
                    for hook in hooks:
                        try:hook()
                        except Exception:pass
                    return
        self.thread=threading.Thread(target=watch,daemon=True);self.thread.start();return self
    def track(self,hook):
        with self.lock:self.hooks.append(hook);stopped=self.stopped.is_set()
        if stopped:hook();self.check()
    def check(self):
        if self.stopped.is_set() or self.cancelled():raise DiagnosticInputError('Test cancelled or timed out. No chat/tool work was submitted.', 'cancelled')
    def __exit__(self,*args):self.done.set();self.thread.join(timeout=.2)

@contextmanager
def sample_audio(audio,config,owner):
    if audio:
        yield Path(audio).expanduser();return
    from odessy.voice.native_audio import recorder
    from odessy.voice.session import pcm_to_wav
    stream=recorder(config);owner.track(stream.close);owner.check()
    try:
        stream.start();owner.check()
        pcm=bytearray()
        while len(pcm)<16000*2*5:
            owner.check();chunk=stream.read(min(2560,16000*2*5-len(pcm)))
            if not chunk:raise RuntimeError('Microphone stopped before recording completed')
            pcm.extend(chunk)
    finally:stream.close()
    with tempfile.TemporaryDirectory(prefix='odessy-input-test-') as folder:
        path=Path(folder)/'input.wav';path.write_bytes(pcm_to_wav(bytes(pcm)));yield path


def voice_config(selection):
    values=asdict(selection.voice)
    values.pop('enabled')
    return VoiceConfig(**values)


def observe(selection,section):
    if section=='llm':
        model=selection.model
        if model is None:return {'status':'not_configured','message':'Choose a model.'}
        key=model.credential_env or KEY_ENV.get(model.provider)
        if key and not credential_value(model):
            return {'status':'missing','code':'credentials','message':f'Configure {key} in Models and connections, or supply that environment variable.'}
        return {'status':'configured_unverified','message':'Configuration present. An explicit test is required; tool compatibility is a separate check.'}
    voice=selection.voice
    if section in ('stt','tts'):
        selector=getattr(voice,section+'_provider')
        keys={'whisper.cpp':('whisper_bin','whisper_model'),'kokoro':('kokoro_model','kokoro_voices')}.get(selector,())
        missing=[]
        for key in keys:
            value=getattr(voice,key)
            found=shutil.which(os.path.expanduser(value)) if key.endswith('_bin') else value and Path(value).expanduser().is_file()
            if not found:missing.append(key)
        if selector=='kokoro':
            missing.extend(name for name in ('kokoro_onnx','onnxruntime') if importlib.util.find_spec(name) is None)
        return {'status':'missing' if missing else 'configured_unverified','message':
                'Missing: '+', '.join(missing) if missing else 'Provider configured; no provider code executed. Test explicitly to verify it.'}
    if section=='voice':
        from odessy.voice.native_audio import inventory
        return inventory(voice.audio_backend)
    return {'status':'configured_unverified','message':'Host settings do not establish pairing, connectivity or workspace permission.'}


def test_model(selection):
    model=selection.model
    if model is None:raise ValueError('Choose a model before testing.')
    if model.provider in ('ollama','lfm'):
        from odessy.models.ollama_model import OllamaModel
        adapter=OllamaModel(model=model.name,endpoint=model.endpoint,text_only=True,
                            timeout=15,call_timeout=20,num_predict=256,keep_alive=0)
        return {'text':adapter.propose('Say hello briefly.').text,'usage':None}
    from odessy.models.cloud_model import build_cloud_model
    from odessy.models.usage import process_ledger
    env=dict(os.environ,ODESSY_CLOUD_MODEL=model.name,ODESSY_CLOUD_RETRIES='0',
             ODESSY_CLOUD_TIMEOUT='15',ODESSY_CLOUD_MAX_TOKENS='256',ODESSY_CLOUD_OPENAI_MAX_TOKENS='256')
    if model.endpoint:env['ODESSY_CLOUD_BASE_URL']=model.endpoint
    adapter=build_cloud_model(model.provider,env,credential_ref=model.credential_env)
    try:
        text=adapter.test_connection()
        if adapter.api_key:
            text = text.replace(adapter.api_key, '<redacted>')
        return {'text':text,'usage':asdict(process_ledger().total),'accounting':'separate diagnostic run'}
    finally:
        client=getattr(adapter,'_client',None)
        if client is not None and callable(getattr(client,'close',None)):client.close()


def probe(selection,section,*,run=False,consent=False,audio='',cancelled=lambda:False):
    with ProbeCancellation(cancelled) as owner:
        return _probe(selection,section,run=run,consent=consent,audio=audio,owner=owner)

def _probe(selection,section,*,run,consent,audio,owner):
    if section not in ('llm','stt','tts','voice','host'):raise ValueError('unknown component')
    from .component_settings import SECTIONS
    from odessy.application.capabilities import digest
    identity=asdict(selection.model) if section=='llm' and selection.model else {
        key:value for key,value in asdict(selection.voice).items() if key in SECTIONS[section]}
    result={'section':section,'fingerprint':digest(identity),'inference_performed':False,**observe(selection,section)}
    if section=='llm' and selection.model:result.update(provider=selection.model.provider,model=selection.model.name)
    if not run:return result
    if consent is not True:raise ValueError('Explicit test consent is required; provider calls may incur cost or open audio.')
    if result.get('code')=='credentials':raise DiagnosticInputError(result['message'],'credentials')
    if section not in ('llm','stt','tts'):raise DiagnosticInputError('This component has no inference test. Use observational checks.','unsupported')
    started=time.monotonic()
    try:
        owner.check()
        if section=='llm':result.update(test_model(selection))
        elif section=='stt':
            with sample_audio(audio,voice_config(selection),owner) as path:
                if not path.is_file() or path.stat().st_size>16000*2*30+65536:
                    raise DiagnosticInputError('Select an existing WAV of at most 30 seconds (16 kHz mono PCM).')
                with wave.open(str(path),'rb') as wav:
                    if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())!=(1,2,16000) or wav.getnframes()>480000:
                        raise DiagnosticInputError('Input must be 16 kHz mono 16-bit PCM, at most 30 seconds.')
                provider=create_stt(voice_config(selection))
                try:
                    owner.track(lambda:cancel_provider(provider))
                    owner.check();answer=provider.transcribe(path)
                    if answer.status=='failed':raise RuntimeError('STT failed; check provider assets and dependencies.')
                    result['text']=answer.text
                    if not answer.text:result['message']='Provider completed; no speech recognized.'
                finally:close_providers(provider)
        elif section=='tts':
            provider=create_tts(voice_config(selection))
            try:
                owner.track(lambda:cancel_provider(provider));owner.check()
                with tempfile.TemporaryDirectory(prefix='odessy-speech-test-') as folder:
                    path=Path(folder)/'output.wav'
                    answer=provider.synthesize('Odessy speech output test.',path)
                    if answer.status!='ok':raise RuntimeError('TTS failed; check provider assets and dependencies.')
                    if not path.is_file() or path.stat().st_size>32*1024*1024:
                        raise DiagnosticInputError('Speech test output is missing or exceeds the size limit.')
                    with wave.open(str(path),'rb') as wav:
                        if (wav.getsampwidth()!=2 or wav.getnchannels() not in (1,2) or
                            not 8000<=wav.getframerate()<=192000 or wav.getnframes()>wav.getframerate()*30):
                            raise DiagnosticInputError('Speech test output must be PCM WAV, mono/stereo, at most 30 seconds.')
                    from odessy.voice.native_audio import player
                    output=player(voice_config(selection));generation=output.generation
                    owner.track(output.stop);owner.check()
                    output.play(path,generation=generation)
            finally:close_providers(provider)
        else:raise ValueError('This component has no inference test.')
        owner.check()
        from odessy.authority.redaction import redact_text,looks_like_secret_name
        if 'text' in result:
            text=result['text']
            key=selection.model.credential_env if selection.model else ''
            secrets={value for name,value in os.environ.items() if value and (looks_like_secret_name(name) or name==key)}
            for secret in sorted(secrets,key=len,reverse=True):text=text.replace(secret,'<redacted>')
            result['text']=redact_text(text)[:2000]
        result.update(status='tested',inference_performed=True,elapsed_ms=round((time.monotonic()-started)*1000),
                      tested_at=time.time(),
                      message=result.get('message') if section=='stt' and not result.get('text') else 'Test completed. This does not verify tool capability or human audibility.')
        return result
    except Exception as error:
        # Provider exception strings can contain credentials, URLs or raw HTTP bodies.
        if isinstance(error,DiagnosticInputError):raise
        raise ProviderTestError('Provider test failed. Check credentials, endpoint, model, assets and dependencies; text interaction remains available.') from None
