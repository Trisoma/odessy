"""One explicit coordinator microphone lease; session/broker retain all authority."""
import asyncio
import contextlib
import uuid
from odessy.voice.interaction import resolve_spoken_answer, voice_allowed
from odessy.interaction.phrases import is_cancel

class VoiceError(RuntimeError):
    def __init__(self,message,code=409):super().__init__(message);self.code=code

class DesktopVoice:
    def __init__(self,audio_factory,*,host):
        self.factory=audio_factory;self.host=host;self.audio=None
        self.owner=None;self.capture_id=None;self.expected=None;self.state='idle';self.error=''
        self._epoch=0;self._decoding=False;self._preparing=False;self._speaking=False;self._playback=None;self._draining=None
        self.wake=None;self._wake_controller=None;self._previous_handler=None;self._wake_handler=None
    @property
    def busy(self):return self.owner is not None or self._playback is not None
    def snapshot(self,controller):
        own=self.owner==controller.id
        return {'supported':True,'state':self.state if own or self.owner is None else 'busy',
                'capture_id':self.capture_id if own else None,'recorder_host':self.host,
                'message':self.error if own or not self.owner else '', 'mode':'wake' if own and self.wake is not None else 'push_to_talk'}
    def _emit(self,c):c.emit('voice_state',self.snapshot(c))
    async def typed(self,c,active):
        if self.wake is not None and self.owner==c.id:await self.wake.set_typed_turn(active)

    async def start_wake(self,c,submit,stop):
        if self.busy or c.running or c.interactions.pending:raise VoiceError('Finish current work and audio before enabling wake listening')
        self.owner=c.id;self._wake_controller=c;self.state='preparing';self._preparing=True
        self._epoch+=1;epoch=self._epoch;self._emit(c)
        try:
            if self.audio is not None:await self.audio.close()
            self.audio=self.factory()
            def status(message):
                if epoch!=self._epoch:return
                self.state={'Listening':'listening','Recording speech':'recording','Transcribing speech':'transcribing','Speaking':'speaking'}.get(message,'listening')
                self._emit(c)
            def failure(stage,message):
                if epoch!=self._epoch:return
                self.error='Wake audio unavailable; disable listening and retry, or use text.'
                self.state='error';self._emit(c)
            self.wake=self.audio.make_wake(submit_voice=submit,stop_turn=stop,interactions=c.interactions,
                report_status=status,report_audio_error=failure)
            self._previous_handler=c.interactions.handler
            async def handle(entry):
                from odessy.voice.hybrid import AudioUnavailable
                try:
                    if not voice_allowed(entry):raise AudioUnavailable('This action requires keyboard or authenticated UI confirmation.')
                    if self.wake is None:raise AudioUnavailable('Wake audio stopped; answer in the application.')
                    await self.wake.handle_interaction(entry)
                except AudioUnavailable as error:
                    # Preserve the same pending request and stronger-channel gate.
                    if c.interactions.is_pending(entry['id']):c.interactions.mark_ready(entry['id'])
                    c.emit('interaction_unavailable',{'id':entry['id'],'message':str(error)})
            self._wake_handler=handle;c.interactions.handler=handle
            available=await self._owned(c,self.wake.start())
            if epoch!=self._epoch:raise VoiceError('Wake startup superseded',410)
            if not available:raise VoiceError('Wake input unavailable. Check microphone, wake model, VAD and speech dependencies.',503)
            self.state='listening';self.error='';self._emit(c)
            return self.snapshot(c)
        except Exception:
            await self._stop_wake();self.owner=None;self.state='error';self._emit(c);raise
        finally:self._preparing=False

    async def _stop_wake(self):
        wake,self.wake=self.wake,None
        controller,self._wake_controller=self._wake_controller,None
        if controller is not None and wake is not None:
            if controller.interactions.handler is self._wake_handler:controller.interactions.handler=self._previous_handler
        self._previous_handler=None
        self._wake_handler=None
        if wake is not None:await wake.aclose(preserve_turn=True)
        if controller is not None:
            for entry in controller.interactions.pending:
                if controller.interactions.is_pending(entry['id']):controller.interactions.mark_ready(entry['id'])
        if self.audio is not None:await self.audio.close()
        self.audio=None
    async def _owned(self,c,operation):
        task=asyncio.create_task(operation)
        try:return await asyncio.shield(task)
        except asyncio.CancelledError:
            self._epoch+=1;self.state='cancelling';self._emit(c)
            async def drain():
                try:
                    await self._cancel_audio()
                    await asyncio.gather(task,return_exceptions=True)
                    await self._cancel_audio()
                finally:
                    self.owner=None;self.capture_id=None;self.expected=None;self.state='idle';self._draining=None;self._emit(c)
            self._draining=asyncio.create_task(drain())
            raise
    def _entry(self,c,identifier):
        return next((e for e in c.interactions.pending if e['id']==identifier and c.interactions.is_pending(identifier)),None)
    async def start(self,c,expected=None,*,preview=False):
        if self.busy:raise VoiceError('Audio is already owned by another capture or playback')
        pending=c.interactions.pending
        if preview and pending:raise VoiceError('Answer the pending interaction before testing speech input')
        entry=self._entry(c,expected) if expected else None
        if pending and entry is None:raise VoiceError('Select the exact pending question or approval before recording')
        if entry is not None and not voice_allowed(entry):raise VoiceError('This action requires keyboard or authenticated UI confirmation.',403)
        if c.running and entry is None:raise VoiceError('Finish or stop the current task before recording')
        self._epoch+=1;epoch=self._epoch;self.owner=c.id;self.expected=expected
        self.capture_id=uuid.uuid4().hex;self.state='preparing';self.error='';self._emit(c)
        self.preview=preview
        try:
            self._preparing=True
            if self.audio is None:self.audio=self.factory()
            await self._owned(c,self.audio.start())
            if epoch!=self._epoch:raise VoiceError('Capture superseded',410)
            if entry is not None:c.interactions.mark_ready(entry['id'])
            self.state='recording';self._emit(c);return self.snapshot(c)
        except Exception:
            await self._cancel_audio();self.owner=None;self.state='error';self.error='Audio input unavailable. Typed input remains available.';self._emit(c)
            raise
        finally:self._preparing=False
    async def finish(self,c,identifier,submit):
        if self.owner!=c.id or identifier!=self.capture_id or self.state!='recording':raise VoiceError('Capture is stale or already consumed',410)
        self.state='transcribing';self._decoding=True;epoch=self._epoch;self._emit(c)
        try:
            text=await self._owned(c,self.audio.finish())
            if epoch!=self._epoch:raise VoiceError('Transcription superseded',410)
            if not text.strip():raise VoiceError('No speech recognized. Retry recording or type instead.',422)
            c.emit('voice_transcript',{'text':c._public(text),'capture_id':identifier})
            if self.preview:
                c.emit('voice_test_result',{'text':c._public(text),'source':'speech_input_test'})
            elif self.expected:
                entry=self._entry(c,self.expected)
                if entry is None:raise VoiceError('Interaction already resolved or expired',410)
                if is_cancel(text):await c.stop()
                elif not resolve_spoken_answer(entry,c.interactions,text):raise VoiceError('Answer not accepted. Positive approval requires the exact displayed phrase.',422)
            elif c.interactions.pending:raise VoiceError('A new interaction needs a fresh recording',410)
            elif is_cancel(text):await c.stop()
            else:await submit(c,text)
            self.state='idle';self.error='';return self.snapshot(c)
        except VoiceError as error:
            self.state='error';self.error=str(error);raise
        except Exception:
            self.error='Transcription unavailable. Typed input remains available.';self.state='error'
            raise VoiceError(self.error,503) from None
        finally:
            self._decoding=False
            if self._draining is None:self.owner=None;self.capture_id=None;self.expected=None
            self._emit(c)
    async def _cancel_audio(self):
        if self.wake is not None:await self._stop_wake()
        if self.audio is not None:
            with contextlib.suppress(Exception):await self.audio.cancel()
    async def cancel(self,c,identifier=None):
        if self.owner is not None and self.owner!=c.id:raise VoiceError('Another session owns audio')
        if identifier is not None and identifier!=self.capture_id:raise VoiceError('Capture is stale',410)
        self._epoch+=1;await self._cancel_audio()
        if not self._decoding and not self._preparing and not self._speaking and self._draining is None:self.owner=None;self.capture_id=None;self.expected=None;self.state='idle'
        else:self.state='cancelling'
        self._emit(c);return self.snapshot(c)
    async def stop_playback(self,c):
        if self.owner is not None and self.owner!=c.id:raise VoiceError('Another session owns audio')
        if self.wake is not None:self.wake.stop_speaking()
        elif self._speaking:await self._cancel_audio()
        self._emit(c);return self.snapshot(c)
    async def speak(self,c,text):
        if self.busy:raise VoiceError('Finish capture before playback')
        self.owner=c.id;self.state='speaking';self._speaking=True;self._emit(c)
        try:
            if self.audio is None:self.audio=self.factory()
            await self._owned(c,self.audio.speak(text))
        except Exception:
            self.error='Audio output unavailable. The completed text answer was not retried.'
            c.emit('audio_error',{'message':self.error})
        finally:
            self._speaking=False
            if self._draining is None:self.owner=None;self.state='idle'
            self._emit(c)
    async def close(self):
        self._epoch+=1
        if self.wake is not None:await self._stop_wake()
        if self.audio is not None:await self.audio.close()
        if self._draining is not None:
            await asyncio.wait({self._draining},timeout=6)
        self.owner=None
