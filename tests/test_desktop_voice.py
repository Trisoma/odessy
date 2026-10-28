import asyncio
import pytest
from odessy.application.session import SessionController

class Audio:
    def __init__(self,text='hello'):self.text=text;self.closed=False;self.started=0
    async def start(self):self.started+=1
    async def finish(self):return self.text
    async def cancel(self):self.closed=True
    async def speak(self,text):pass
    async def close(self):self.closed=True

def controller():return SessionController(model_factory=lambda _:object(),agent_factory=lambda *a,**k:object(),profiles={'local':{}})

def test_wake_owns_one_microphone_and_uses_the_same_broker_until_explicit_stop():
    from odessy.application.desktop_voice import DesktopVoice, VoiceError
    class Wake:
        input_available=True
        def __init__(self):self.closed=False;self.typed=[]
        async def start(self):return True
        async def aclose(self,**options):self.closed=True
        async def handle_interaction(self,entry):pass
        async def set_typed_turn(self,value):self.typed.append(value)
    class WakeAudio(Audio):
        def make_wake(self,**options):self.options=options;self.wake=Wake();return self.wake
    async def run():
        a,b=controller(),controller();audio=WakeAudio();v=DesktopVoice(lambda:audio,host='local')
        async def submit(text,stop,context):return 'answer'
        async def stop():pass
        await v.start_wake(a,submit,stop)
        assert v.snapshot(a)['mode']=='wake' and v.busy
        assert audio.options['interactions'] is a.interactions
        with pytest.raises(VoiceError):await v.start(b)
        await v.typed(a,True);assert audio.wake.typed==[True]
        await v.cancel(a)
        assert audio.wake.closed and not v.busy and a.interactions.handler is None
        await v.close();await a.close();await b.close()
    asyncio.run(run())

def test_wake_stronger_channel_and_disabling_audio_preserve_pending_ui_answer():
    from odessy.application.desktop_voice import DesktopVoice
    class Wake:
        async def start(self):return True
        async def aclose(self,**options):assert options['preserve_turn'] is True
        async def handle_interaction(self,entry):raise AssertionError('stronger channel must not enter audio')
    class WakeAudio(Audio):
        def make_wake(self,**options):return Wake()
    async def run():
        c=controller();v=DesktopVoice(WakeAudio,host='local')
        await v.start_wake(c,lambda *a:None,lambda:None)
        c.interactions.start_turn()
        question=asyncio.create_task(c.interactions.request_approval({'prompt':'Expand sandbox','risk':'high','allowed_channels':['authenticated_ui']}))
        await asyncio.sleep(.01)
        entry=c.interactions.pending[0]
        assert entry['ready'] and c.interactions.is_pending(entry['id'])
        await v.cancel(c)
        assert c.interactions.is_pending(entry['id'])
        c.interactions.cancel_pending();assert await question is False
        await v.close();await c.close()
    asyncio.run(run())

def test_api_stop_playback_stops_wake_output_without_releasing_listener():
    httpx=pytest.importorskip('httpx')
    from odessy.application.desktop_voice import DesktopVoice
    from odessy.application.api import create_app
    class Wake:
        def __init__(self):self.stops=0
        def stop_speaking(self):self.stops+=1
        async def aclose(self,**options):pass
    async def run():
        v=DesktopVoice(Audio,host='local');app=create_app(controller,token='secret',desktop_voice=v)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer secret'}) as client:
            sid=(await client.post('/v1/sessions')).json()['id']
            wake=Wake();v.wake=wake;v.owner=sid;v.state='speaking'
            response=await client.post(f'/v1/sessions/{sid}/voice',json={'action':'stop_playback'})
            assert response.status_code==200 and wake.stops==1
            assert v.wake is wake and v.owner==sid
            await v.close()
    asyncio.run(run())

def test_capture_is_one_use_session_owned_and_cancel_never_submits():
    from odessy.application.desktop_voice import DesktopVoice, VoiceError
    async def run():
        a,b=controller(),controller();audio=Audio();voice=DesktopVoice(lambda:audio,host='test-host')
        assert voice.snapshot(a)['state']=='idle';assert audio.started==0
        started=await voice.start(a)
        with pytest.raises(VoiceError):await voice.start(b)
        calls=[]
        async def submit(c,text):calls.append((c.id,text))
        await voice.finish(a,started['capture_id'],submit)
        assert calls==[(a.id,'hello')]
        with pytest.raises(VoiceError):await voice.finish(a,started['capture_id'],submit)
        next_=await voice.start(a);await voice.cancel(a,next_['capture_id'])
        assert len(calls)==1
        await voice.close();await a.close();await b.close()
    asyncio.run(run())

def test_bare_yes_and_stronger_channel_are_not_voice_authorizations():
    from odessy.application.desktop_voice import DesktopVoice, VoiceError
    async def run():
        c=controller();voice=DesktopVoice(lambda:Audio('yes'),host='test')
        c.interactions.start_turn()
        pending=asyncio.create_task(c.interactions.request_approval({'prompt':'Change file','risk':'medium','allowed_channels':['voice','authenticated_ui']}))
        await asyncio.sleep(0)
        entry=c.interactions.pending[0]
        capture=await voice.start(c,entry['id'])
        with pytest.raises(VoiceError):await voice.finish(c,capture['capture_id'],None)
        assert c.interactions.is_pending(entry['id'])
        c.interactions.cancel_pending();await pending
        await voice.close();await c.close()
    asyncio.run(run())

def test_real_api_routes_speech_once_into_existing_session_and_keeps_text_available():
    httpx=pytest.importorskip('httpx')
    from odessy.application.desktop_voice import DesktopVoice
    from odessy.application.api import create_app
    from odessy.runtime.types import AgentTurnResult
    class Agent:
        def __init__(self,*a,**kw):pass
        async def run_turn(self,text,**kw):return AgentTurnResult('answer:'+text,[])
    async def run():
        audio=Audio('hello');voice=DesktopVoice(lambda:audio,host='local-test')
        app=create_app(lambda:SessionController(model_factory=lambda _:object(),agent_factory=Agent,profiles={'local':{}}),token='secret',desktop_voice=voice)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer secret'}) as client:
            sid=(await client.post('/v1/sessions')).json()['id'];route=f'/v1/sessions/{sid}'
            assert audio.started==0
            capture=(await client.post(route+'/voice',json={'action':'start_capture'})).json()
            assert (await client.post(route+'/voice',json={'action':'cancel_capture'})).status_code==400
            assert (await client.get(route+'/voice')).json()['state']=='recording'
            assert (await client.post(route+'/voice',json={'action':'finish_capture','capture_id':capture['capture_id']})).status_code==200
            await asyncio.sleep(.01)
            assert len((await client.get(route)).json()['history'])==1
            assert (await client.post(route+'/voice',json={'action':'finish_capture','capture_id':capture['capture_id']})).status_code==410
            assert (await client.post(route+'/turns',json={'text':'typed'})).status_code==202
            await asyncio.sleep(.01)
            assert len((await client.get(route)).json()['history'])==2
            preview=(await client.post(route+'/voice',json={'action':'test_input'})).json()
            assert (await client.post(route+'/voice',json={'action':'finish_capture','capture_id':preview['capture_id']})).status_code==200
            await asyncio.sleep(.01)
            assert len((await client.get(route)).json()['history'])==2, 'Input test must not invoke the model'
            await client.delete(route)
        await voice.close()
    asyncio.run(run())

@pytest.mark.parametrize('text,approved',[('approve this action',True),('I deny.',False)])
def test_spoken_answers_resolve_only_the_bound_medium_request(text,approved):
    from odessy.application.desktop_voice import DesktopVoice,VoiceError
    async def run():
        c=controller();v=DesktopVoice(lambda:Audio(text),host='local');c.interactions.start_turn()
        pending=asyncio.create_task(c.interactions.request_approval({'prompt':'Validated change','risk':'medium','allowed_channels':['voice','authenticated_ui']}))
        await asyncio.sleep(0);entry=c.interactions.pending[0]
        started=await v.start(c,entry['id']);await v.finish(c,started['capture_id'],None)
        assert await pending is approved
        strong=asyncio.create_task(c.interactions.request_approval({'prompt':'Validated high risk','risk':'high','allowed_channels':['authenticated_ui']}))
        await asyncio.sleep(0);entry=c.interactions.pending[0]
        with pytest.raises(VoiceError) as failure:await v.start(c,entry['id'])
        assert failure.value.code==403;assert c.interactions.is_pending(entry['id'])
        c.interactions.cancel_pending();await strong;await v.close();await c.close()
    asyncio.run(run())

def test_late_transcript_cannot_answer_a_question_already_resolved_by_ui():
    from odessy.application.desktop_voice import DesktopVoice,VoiceError
    class Slow(Audio):
        async def finish(self):return await result
    async def run():
        nonlocal_result=None
        c=controller();v=DesktopVoice(lambda:Slow(),host='local');c.interactions.start_turn()
        pending=asyncio.create_task(c.interactions.ask('Which project?'));await asyncio.sleep(0)
        entry=c.interactions.pending[0];capture=await v.start(c,entry['id'])
        c.interactions.resolve_question(entry['id'],'Odessy',channel='authenticated_ui')
        assert (await pending)['answer']=='Odessy'
        result.set_result('Other project')
        with pytest.raises(VoiceError) as failure:await v.finish(c,capture['capture_id'],None)
        assert failure.value.code==410
        await v.close();await c.close()
    async def main():
        nonlocal result
        result=asyncio.get_running_loop().create_future();await run()
    result=None
    asyncio.run(main())

def test_cancelling_playback_keeps_lease_until_worker_drains():
    from odessy.application.desktop_voice import DesktopVoice,VoiceError
    async def run():
        release=asyncio.Event()
        class Slow(Audio):
            async def speak(self,text):await release.wait()
        a,b=controller(),controller();v=DesktopVoice(lambda:Slow(),host='local')
        speaking=asyncio.create_task(v.speak(a,'reply'));await asyncio.sleep(0)
        await v.cancel(a)
        with pytest.raises(VoiceError):await v.start(b)
        release.set();await speaking
        assert not v.busy
        await v.close();await a.close();await b.close()
    asyncio.run(run())

@pytest.mark.parametrize('stage',['start','finish'])
def test_cancelled_request_retains_audio_lease_until_worker_finishes(stage):
    from odessy.application.desktop_voice import DesktopVoice,VoiceError
    async def run():
        release=asyncio.Event();entered=asyncio.Event()
        class Slow(Audio):
            async def start(self):
                if stage=='start':entered.set();await release.wait()
            async def finish(self):
                entered.set();await release.wait();return 'late'
        a,b=controller(),controller();v=DesktopVoice(lambda:Slow(),host='local')
        if stage=='start':task=asyncio.create_task(v.start(a))
        else:
            capture=await v.start(a);task=asyncio.create_task(v.finish(a,capture['capture_id'],None))
        await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        with pytest.raises(VoiceError):await v.start(b)
        release.set();await asyncio.sleep(.02)
        assert not v.busy
        await v.close();await a.close();await b.close()
    asyncio.run(run())

