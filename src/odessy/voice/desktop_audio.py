"""Bounded explicit capture over existing audio/provider ownership; no agent loop."""
import asyncio
import contextlib
from .capture import CommandAudioStream
from .frames import SAMPLE_RATE
from .hybrid import HybridAudioAdapter

async def _unused(*args):raise RuntimeError('Desktop audio cannot submit model work')

class DesktopAudio:
    def __init__(self,config):
        self.config=config
        self.adapter=HybridAudioAdapter(config,_unused,listen=False)
        self.stream=None;self.reader=None;self.buffer=bytearray();self.stopping=False
        self.errors=[]
        self.adapter.report_audio_error=lambda stage,message:self.errors.append(stage)

    async def start(self):
        if self.reader is not None and not self.reader.done():raise RuntimeError('Previous capture is still draining')
        await self.cancel()
        self.errors.clear();self.buffer.clear();self.stopping=False
        # Stop playback and settle before a fresh recorder is admitted: no pre-cue buffer.
        await asyncio.sleep(self.config.settling_ms/1000)
        from .native_audio import recorder
        stream=recorder(self.config,command_factory=CommandAudioStream)
        self.stream=stream
        await asyncio.to_thread(stream.start)
        self.reader=asyncio.create_task(self._read(stream))

    async def _read(self,stream):
        limit=min(self.config.max_utterance_seconds,30)*SAMPLE_RATE*2
        try:
            while not self.stopping and len(self.buffer)<limit:
                chunk=await asyncio.to_thread(stream.read,min(2048,limit-len(self.buffer)))
                if not chunk:break
                self.buffer.extend(chunk)
        except Exception:
            if not self.stopping:self.errors.append('capture')
        finally:
            await asyncio.to_thread(stream.close)

    async def _stop_reader(self):
        self.stopping=True
        stream,self.stream=self.stream,None
        if stream is not None:await asyncio.to_thread(stream.close)
        reader=self.reader
        if reader is not None:
            await asyncio.wait_for(asyncio.shield(reader),6)
            self.reader=None

    async def finish(self):
        await self._stop_reader()
        if self.errors:raise RuntimeError('Microphone capture failed')
        pcm=bytes(self.buffer);self.buffer.clear()
        if not pcm:return ''
        return await self.adapter.transcribe_pcm(pcm)

    async def cancel(self):
        self.adapter.stop_speaking();self.adapter.cancel_transcription()
        await self._stop_reader();self.buffer.clear()

    async def speak(self,text):
        self.errors.clear()
        await self.adapter.speak_once(text)
        if self.errors:raise RuntimeError('Speech output failed')

    def make_wake(self,**options):
        return HybridAudioAdapter(self.config,options['submit_voice'],listen=True,
            stop_turn=options['stop_turn'],interactions=options['interactions'],
            report_status=options['report_status'],report_audio_error=options['report_audio_error'])

    async def close(self):
        with contextlib.suppress(Exception):await self.cancel()
        await self.adapter.aclose()
