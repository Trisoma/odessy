"""Phrase onset and cancellation through the real audio adapter; no hardware."""
import asyncio
import threading

from odessy.voice.config import VoiceConfig
from odessy.voice.hybrid import HybridAudioAdapter
from odessy.voice.tts import TTSResult


def test_first_phrase_plays_before_last_synthesis_and_stop_discards_lookahead(tmp_path):
    async def run():
        events = []
        second_started, release = threading.Event(), threading.Event()
        class TTS:
            def synthesize(self, text, path):
                events.append(('synth', text))
                path.write_bytes(b'RIFF')
                if text == 'Second sentence.':
                    second_started.set()
                    release.wait(3)
                return TTSResult(path, 'ok')
        class Player:
            generation = 0
            def play(self, path, **kwargs):
                events.append(('play', path.name))
                assert not any(e == ('synth', 'Third sentence.') for e in events)
                adapter.stop_speaking()
                release.set()
            def stop(self): self.generation += 1
        async def submit(*args): return ''
        adapter = HybridAudioAdapter(VoiceConfig(whisper_bin=None, whisper_model=None, kokoro_model=None, kokoro_voices=None, tmp_dir=tmp_path), submit)
        adapter._started = True
        adapter._tts, adapter._player = TTS(), Player()
        await asyncio.wait_for(adapter.speak('First sentence. Second sentence. Third sentence.'), 5)
        assert events[0] == ('synth', 'First sentence.')
        assert sum(e[0] == 'play' for e in events) == 1
        assert not list(tmp_path.glob('*.wav'))
    asyncio.run(run())


def test_phrase_order_and_files_cleaned(tmp_path):
    async def run():
        heard = []
        class TTS:
            def synthesize(self, text, path):
                path.write_text(text)
                return TTSResult(path, 'ok')
        class Player:
            def play(self, path, **kwargs): heard.append(path.read_text())
        async def submit(*args): return ''
        adapter = HybridAudioAdapter(VoiceConfig(whisper_bin=None, whisper_model=None, kokoro_model=None, kokoro_voices=None, tmp_dir=tmp_path), submit)
        adapter._started = True
        adapter._tts, adapter._player = TTS(), Player()
        await adapter.speak('First sentence. Second sentence. Third sentence.')
        assert heard == ['First sentence.', 'Second sentence.', 'Third sentence.']
        assert not list(tmp_path.glob('*.wav'))
    asyncio.run(run())


def test_cancel_does_not_unlink_file_still_owned_by_synthesis(tmp_path):
    async def run():
        entered,release=threading.Event(),threading.Event()
        class TTS:
            def synthesize(self,text,path):
                path.write_bytes(b'RIFF');entered.set();release.wait(3)
                assert path.exists()
                return TTSResult(path,'ok')
        class Player:
            def play(self,*args,**kwargs):raise AssertionError('obsolete speech')
            def stop(self):pass
        async def submit(*args):return ''
        adapter=HybridAudioAdapter(VoiceConfig(None,None,None,None,tmp_dir=tmp_path),submit)
        adapter._started=True;adapter._tts=TTS();adapter._player=Player()
        task=asyncio.create_task(adapter.speak('First. Second.'))
        while not entered.is_set():await asyncio.sleep(.001)
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        assert list(tmp_path.glob('*.wav'))
        release.set();await adapter._drain_blocking()
        assert not list(tmp_path.glob('*.wav'))
    asyncio.run(run())


def test_long_completed_answers_split_before_later_punctuation():
    from odessy.runtime.speech import speech_phrases
    text=('A long sentence with no early punctuation ' * 30)+'.'
    phrases=list(speech_phrases(text))
    assert len(phrases)>1 and all(len(p)<=161 for p in phrases)
    assert ' '.join(phrases)==text
