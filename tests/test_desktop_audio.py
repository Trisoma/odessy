import asyncio
import threading
import wave
from pathlib import Path
from odessy.voice.config import VoiceConfig
from odessy.voice.stt import STTResult


def test_explicit_capture_refreshes_hotplug_inventory_before_opening(monkeypatch):
    from types import SimpleNamespace
    from odessy.voice import native_audio
    from odessy.voice.desktop_audio import DesktopAudio
    rows=[];opened=[]
    def initialize():
        rows.append(dict(name='Headset',hostapi=0,max_input_channels=1,max_output_channels=0))
    class Stream:
        def __init__(self,**options):opened.append(options['device'])
        def start(self):pass
        def abort(self):pass
        def close(self):pass
    module=SimpleNamespace(_terminate=lambda:None,_initialize=initialize,
        query_devices=lambda:rows,query_hostapis=lambda:[{'name':'test'}],RawInputStream=Stream)
    monkeypatch.setitem(__import__('sys').modules,'sounddevice',module)
    async def run():
        a=DesktopAudio(VoiceConfig('','','','',audio_backend='native',audio_input_device='test::Headset',settling_ms=0))
        try:
            await a.start()
            assert opened==[0]
        finally:await a.close()
    asyncio.run(run())


def test_explicit_playback_refreshes_hotplug_and_plays_through_selected_output(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from odessy.voice.hybrid import HybridAudioAdapter
    rows=[];played=[]
    def initialize():rows.append(dict(name='Headset',hostapi=0,max_input_channels=0,max_output_channels=2))
    class Stream:
        def __init__(self,**options):assert options['device']==0
        def start(self):pass
        def stop(self):pass
        def abort(self):pass
        def close(self):pass
        def write(self,data):played.append(data)
    module=SimpleNamespace(_terminate=lambda:None,_initialize=initialize,
        query_devices=lambda:rows,query_hostapis=lambda:[{'name':'test'}],RawOutputStream=Stream)
    monkeypatch.setitem(__import__('sys').modules,'sounddevice',module)
    class TTS:
        def synthesize(self,text,path):
            with wave.open(str(path),'wb') as wav:
                wav.setparams((1,2,16000,0,'NONE',''));wav.writeframes(b'\x01\x00'*1600)
            return SimpleNamespace(status='ok')
        def close(self):pass
    async def run():
        adapter=HybridAudioAdapter(VoiceConfig('','','','',audio_backend='native',
            audio_output_device='test::Headset',tmp_dir=tmp_path),lambda *a:None,tts_factory=TTS)
        try:
            await adapter.speak_once('Test output.')
            assert sum(map(len,played))==3200
            assert not adapter._output_failure
        finally:await adapter.aclose()
    asyncio.run(run())

def test_explicit_pcm_provider_uses_wav_and_never_initializes_wake(tmp_path,monkeypatch):
    from odessy.voice.hybrid import HybridAudioAdapter
    from odessy.voice import hybrid
    calls=[]
    class Speech:
        def transcribe(self,path):
            with wave.open(str(path)) as f:assert f.getframerate()==16000 and f.getnchannels()==1
            calls.append(path);return STTResult('hello','ok')
        def close(self):pass
    monkeypatch.setattr(hybrid,'create_stt',lambda *a,**kw:Speech())
    async def run():
        a=HybridAudioAdapter(VoiceConfig('','','','',tmp_dir=tmp_path),lambda *a:None,wake_factory=lambda:(_ for _ in ()).throw(AssertionError('wake was initialized')))
        assert await a.transcribe_pcm(bytes(3200))=='hello'
        assert await a.transcribe_pcm(bytes(3200))=='hello'
        assert len(calls)==2;assert not list(tmp_path.glob('*.wav'))
        await a.aclose()
    asyncio.run(run())

def test_stop_during_output_initialization_prevents_obsolete_playback(tmp_path):
    from odessy.voice.hybrid import HybridAudioAdapter
    initialized=threading.Event();release=threading.Event();played=[]
    class Player:
        generation=0
        def stop(self):self.generation+=1
        def play(self,*a,**kw):played.append(a)
    class Speech:
        def synthesize(self,*a):raise AssertionError('obsolete synthesis must not start')
        def close(self):pass
    def factory():initialized.set();release.wait(3);return Speech()
    async def run():
        a=HybridAudioAdapter(VoiceConfig('','','','',tmp_dir=tmp_path),lambda *a:None,tts_factory=factory,player_factory=Player)
        pending=asyncio.create_task(a.speak_once('obsolete'))
        await asyncio.to_thread(initialized.wait,2);a.stop_speaking();release.set();await pending
        assert played==[];await a.aclose()
    asyncio.run(run())
