import asyncio
import threading
import wave
from pathlib import Path
from odessy.voice.config import VoiceConfig
from odessy.voice.stt import STTResult

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
