import asyncio
import io
import pytest
from odessy.voice.interaction import interaction_prompt, resolve_spoken_answer
from odessy.interaction.terminal import TerminalInteractionHandler, TerminalUnavailable

@pytest.fixture
def anyio_backend(): return 'asyncio'

class Broker:
    def __init__(self): self.answers=[]; self.pending=True; self.ready=[]
    def mark_ready(self, id): self.ready.append(id)
    def is_pending(self, id): return self.pending
    def resolve_approval(self,id,value,channel='voice'):
        self.answers.append((id,value,channel)); self.pending=False; return True
    def resolve_question(self,id,value,channel='voice'):
        self.answers.append((id,value,channel)); self.pending=False; return True
    def cancel_pending(self,reason): self.pending=False

def entry(**kw): return dict(kind='approval',id='a',turn_id='t',prompt='Run command',risk='medium',allowed_channels=['voice','terminal'],**kw)

def test_prompts_do_not_speak_acceptance_phrase():
    assert interaction_prompt(entry()).startswith('Odessy requests permission.')
    assert 'approve this action' not in interaction_prompt(entry()).lower()
    assert interaction_prompt({'kind':'question','question':'Which file?'}).startswith('I need one detail before I continue.')

def test_voice_acceptance_is_exact_and_medium_only():
    b=Broker()
    assert not resolve_spoken_answer(entry(), b, 'yes')
    assert not b.answers
    assert resolve_spoken_answer(entry(), b, ' Approve this action! ')
    assert b.answers == [('a',True,'voice')]
    b=Broker(); high=entry(); high['risk']='high'
    assert not resolve_spoken_answer(high,b,'approve this action')
    assert b.pending

@pytest.mark.anyio
async def test_terminal_non_tty_is_explicitly_unavailable():
    with pytest.raises(TerminalUnavailable):
        await TerminalInteractionHandler(Broker(),stdin=io.StringIO('yes\n'),stdout=io.StringIO())(entry())

@pytest.mark.anyio
async def test_wake_pending_answer_is_local_not_a_new_turn():
    from test_wake_session import Harness
    from dataclasses import replace
    h=Harness([]); session=h.build(); session.options=replace(session.options,settling_ms=0)
    b=Broker(); task=asyncio.create_task(session.handle_interaction(entry(),b))
    await asyncio.sleep(0); await asyncio.sleep(0)
    assert session.state == 'RECORDING'
    await session._submit('approve this action',session._turn_id)
    await task
    assert b.answers == [('a',True,'voice')]
    assert not h.turns and session._pending is None
    assert 'approve this action' not in ' '.join(h.spoken).lower()

@pytest.mark.anyio
async def test_wake_question_retries_do_not_submit_to_model():
    from test_wake_session import Harness
    from dataclasses import replace
    h=Harness([]); session=h.build(); session.options=replace(session.options,settling_ms=0)
    b=Broker(); question={'kind':'question','id':'q','question':'Which?', 'risk':'low','allowed_channels':['voice'],'allow_free_text':False,'choices':['one','two']}
    task=asyncio.create_task(session.handle_interaction(question,b))
    await asyncio.sleep(0); await asyncio.sleep(0)
    await session._submit('wrong',session._turn_id)
    assert not b.answers and not h.turns
    await session._submit('two',session._turn_id)
    await task
    assert b.answers == [('q','two','voice')]

@pytest.mark.anyio
async def test_pending_voice_stop_cancels_own_turn():
    from test_wake_session import Harness
    from dataclasses import replace
    h=Harness([]); session=h.build(); session.options=replace(session.options,settling_ms=0)
    b=Broker(); task=asyncio.create_task(session.handle_interaction(entry(),b))
    await asyncio.sleep(0); await asyncio.sleep(0)
    await session._submit('cancel',session._turn_id)
    await task
    assert session._stop.is_set() and not b.pending

@pytest.mark.anyio
async def test_wake_high_risk_voice_attempt_remains_pending():
    from test_wake_session import Harness
    from dataclasses import replace
    h=Harness([]); session=h.build(); session.options=replace(session.options,settling_ms=0)
    b=Broker(); high=entry(); high['risk']='high'; high['allowed_channels']=['terminal','authenticated_ui']
    task=asyncio.create_task(session.handle_interaction(high,b))
    await asyncio.sleep(0); await asyncio.sleep(0)
    await session._submit('approve this action',session._turn_id)
    assert b.pending and not b.answers
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task

@pytest.mark.anyio
async def test_prompt_suppresses_frames_and_old_stt():
    from test_wake_session import Harness, SPEECH
    from dataclasses import replace
    h=Harness([]); session=h.build(); session.options=replace(session.options,settling_ms=0)
    started=asyncio.Event(); release=asyncio.Event()
    async def transcribe(audio):
        started.set(); await release.wait(); return 'approve this action'
    session.transcribe=transcribe
    session._capture_pending=(b'old',session._turn_id)
    old=asyncio.create_task(session._transcribe_pending()); await started.wait()
    prompt_started=asyncio.Event(); prompt_release=asyncio.Event()
    async def speak(text): prompt_started.set(); await prompt_release.wait()
    session.speak=speak
    b=Broker(); task=asyncio.create_task(session.handle_interaction(entry(),b)); await prompt_started.wait()
    await session._handle_frame(SPEECH)
    assert session.state == 'INTERACTION_PROMPT'
    prompt_release.set(); await asyncio.sleep(0); await asyncio.sleep(0)
    release.set(); await old
    assert not b.answers
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task

@pytest.mark.anyio
async def test_one_shot_question_resumes_with_human_answer(tmp_path):
    from types import SimpleNamespace
    from odessy.voice.loop import VoiceLoop
    from odessy.interaction.broker import InteractionBroker
    broker=InteractionBroker(lambda *args:None,lambda value:value)
    broker.start_turn('t')
    spoken=[]; recordings=[]
    class Audio:
        def record(self,path,seconds): recordings.append(path); path.touch()
        def play(self,path): pass
    class STT:
        def transcribe(self,path): return SimpleNamespace(status='ok',text='the second file')
    class TTS:
        def synthesize(self,text,path): spoken.append(text); path.touch(); return SimpleNamespace(status='ok')
    voice=VoiceLoop(None,Audio(),STT(),TTS(),tmp_path,interactions=broker,settling_ms=0)
    broker.handler=voice.handle_interaction
    answer=await broker.ask('Which file?')
    assert answer['answer']=='the second file' and len(recordings)==1
    assert spoken[0].startswith('I need one detail before I continue.')
    assert list(tmp_path.iterdir())==[]

@pytest.mark.anyio
async def test_terminal_reader_cancellation_is_drained(monkeypatch):
    import odessy.interaction.terminal as terminal
    class Tty(io.StringIO):
        def isatty(self): return True
    started=asyncio.Event(); ended=asyncio.Event()
    async def read(*args):
        started.set()
        try: await asyncio.Future()
        finally: ended.set()
    monkeypatch.setattr(terminal,'read_terminal_line',read)
    task=asyncio.create_task(TerminalInteractionHandler(Broker(),stdin=Tty(),stdout=io.StringIO())(entry()))
    await started.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert ended.is_set()

@pytest.mark.anyio
async def test_handler_race_preserves_voice_when_terminal_unavailable():
    from odessy.interaction.terminal import race_handlers
    b=Broker()
    async def voice(e): b.resolve_approval(e['id'],False,channel='voice')
    await race_handlers(TerminalInteractionHandler(b,stdin=io.StringIO(),stdout=io.StringIO()),voice)(entry())
    assert b.answers==[('a',False,'voice')]

@pytest.mark.anyio
async def test_fresh_wake_interrupts_pending_interaction():
    from test_wake_session import Harness
    from dataclasses import replace
    h=Harness([]); session=h.build(); session.options=replace(session.options,settling_ms=0)
    b=Broker(); task=asyncio.create_task(session.handle_interaction(entry(),b))
    await asyncio.sleep(0); await asyncio.sleep(0)
    old=session._turn_id
    await session._activate(); await task
    assert not b.pending and session._turn_id==old+1
    assert session.state=='RECORDING'

@pytest.mark.anyio
async def test_one_shot_does_not_record_high_risk_voice(tmp_path):
    from types import SimpleNamespace
    from odessy.voice.loop import VoiceLoop
    class Audio:
        def record(self,*args): raise AssertionError('high risk opened microphone')
        def play(self,*args): pass
    class TTS:
        def synthesize(self,*args): return SimpleNamespace(status='ok')
    b=Broker(); high=entry(); high['risk']='high'; high['allowed_channels']=['terminal']
    voice=VoiceLoop(None,Audio(),None,TTS(),tmp_path,interactions=b,settling_ms=0)
    task=asyncio.create_task(voice.handle_interaction(high))
    for _ in range(100):
        if b.ready: break
        await asyncio.sleep(.001)
    assert b.ready==['a'] and b.pending
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task

@pytest.mark.anyio
async def test_cancelled_owned_audio_call_finishes_before_return(tmp_path):
    from odessy.voice.loop import VoiceLoop
    import threading
    started=threading.Event(); release=threading.Event(); ended=threading.Event()
    def record():
        started.set(); release.wait(2); ended.set()
    voice=VoiceLoop(None,None,None,None,tmp_path)
    task=asyncio.create_task(voice._blocking(record))
    while not started.is_set(): await asyncio.sleep(.001)
    task.cancel(); await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError): await task
    assert ended.is_set()

@pytest.mark.anyio
async def test_terminal_can_approve_high_risk(monkeypatch):
    import odessy.interaction.terminal as terminal
    class Tty(io.StringIO):
        def isatty(self): return True
    async def read(*args): return 'approve this action'
    monkeypatch.setattr(terminal,'read_terminal_line',read)
    b=Broker(); high=entry(); high['risk']='high'; high['allowed_channels']=['terminal']
    await TerminalInteractionHandler(b,stdin=Tty(),stdout=io.StringIO())(high)
    assert b.answers==[('a',True,'terminal')]

@pytest.mark.anyio
async def test_audio_cancellation_stops_owned_device(tmp_path):
    import threading
    from odessy.voice.loop import VoiceLoop
    started=threading.Event(); release=threading.Event()
    class Audio:
        def record(self): started.set(); release.wait(2)
        def stop(self): release.set()
    audio=Audio(); voice=VoiceLoop(None,audio,None,None,tmp_path)
    task=asyncio.create_task(voice._blocking(audio.record))
    while not started.is_set(): await asyncio.sleep(.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task,.5)
    assert release.is_set()

@pytest.mark.anyio
async def test_command_audio_stop_terminates_only_owned_process(monkeypatch,tmp_path):
    import threading
    import odessy.voice.audio_io as audio_module
    started=threading.Event(); ended=threading.Event(); created=[]
    class Process:
        returncode=None
        def __init__(self,*args,**kwargs): self.terminated=False; created.append(self)
        def communicate(self,timeout=None): started.set(); ended.wait(2); return ('','')
        def poll(self): return self.returncode
        def terminate(self): self.terminated=True; self.returncode=-15; ended.set()
        def wait(self,timeout=None): return self.returncode
    monkeypatch.setattr(audio_module.subprocess,'Popen',Process)
    audio=audio_module.CommandAudioIO()
    task=asyncio.create_task(asyncio.to_thread(audio.record,tmp_path/'in.wav',1))
    while not started.is_set(): await asyncio.sleep(.001)
    audio.stop()
    with pytest.raises(audio_module.AudioIOError): await task
    assert len(created)==1 and created[0].terminated
    assert not audio._processes

@pytest.mark.anyio
async def test_async_terminal_stop_callback_is_scheduled(monkeypatch):
    import odessy.interaction.terminal as terminal
    class Tty(io.StringIO):
        def isatty(self): return True
    async def read(*args): return 'cancel'
    stopped=asyncio.Event(); b=Broker()
    async def stop():
        assert not b.pending
        stopped.set()
    monkeypatch.setattr(terminal,'read_terminal_line',read)
    await TerminalInteractionHandler(b,stdin=Tty(),stdout=io.StringIO(),stop_turn=stop)(entry())
    await asyncio.wait_for(stopped.wait(),.5)


def test_cancelled_audio_before_start_never_launches_process(monkeypatch,tmp_path):
    import threading
    import odessy.voice.audio_io as audio_module
    def forbidden(*args,**kwargs): raise AssertionError('cancelled operation launched a process')
    monkeypatch.setattr(audio_module.subprocess,'Popen',forbidden)
    audio=audio_module.CommandAudioIO(); cancelled=threading.Event(); cancelled.set()
    with pytest.raises(audio_module.AudioIOError):
        audio.run_cancellable(audio.record,(tmp_path/'input.wav',1),cancelled)

@pytest.mark.anyio
async def test_audio_cancel_during_process_start_is_not_lost(monkeypatch,tmp_path):
    import threading
    import odessy.voice.audio_io as audio_module
    from odessy.voice.loop import VoiceLoop
    started=threading.Event(); release=threading.Event(); terminated=threading.Event()
    class Process:
        returncode=None
        def __init__(self,*args,**kwargs): started.set(); release.wait(2)
        def communicate(self,timeout=None): terminated.wait(2); return ('','')
        def terminate(self): self.returncode=-15; terminated.set()
        def poll(self): return self.returncode
        def wait(self,timeout=None): return self.returncode
    monkeypatch.setattr(audio_module.subprocess,'Popen',Process)
    audio=audio_module.CommandAudioIO(); voice=VoiceLoop(None,audio,None,None,tmp_path)
    task=asyncio.create_task(voice._blocking(audio.record,tmp_path/'input.wav',1))
    while not started.is_set(): await asyncio.sleep(.001)
    task.cancel(); await asyncio.sleep(0); release.set()
    with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task,.5)
    assert terminated.is_set() and not audio._processes

@pytest.mark.anyio
async def test_cli_wake_cancelled_question_allows_next_turn(monkeypatch,tmp_path):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    import odessy.cli as cli
    from odessy.application.session import SessionController
    from odessy.runtime.types import AgentTurnResult
    from odessy.voice.session import STOPPED_TEXT
    from test_wake_lifecycle import setup_cli
    setup_cli(monkeypatch,tmp_path)
    cli.VoiceConfig.from_env().settling_ms=0
    controller=None
    class Agent:
        async def run_turn(self,text,**kwargs):
            if text=='first':
                await controller.interactions.ask('Which file?')
                # Some asyncio versions race a ready wait_for with cancellation;
                # the provider's cooperative stop still cancels this child turn.
                if kwargs['stop_event'].is_set():
                    raise asyncio.CancelledError
            return AgentTurnResult(final_text='answer '+text,log=[])
    controller=SessionController(model_factory=lambda name:object(),
        agent_factory=lambda *args,**kwargs:Agent(), profiles={'local':{}},
        config=SimpleNamespace(participants={}))
    @asynccontextmanager
    async def runtime(**kwargs):
        try: yield controller
        finally: await controller.close()
    monkeypatch.setattr(cli,'controller_runtime',runtime)
    observed=[]
    async def run(self):
        async def pending(entry):
            controller.interactions.mark_ready(entry['id'])
            await asyncio.Future()
        controller.interactions.handler=pending
        first=asyncio.create_task(self.run_turn('first',asyncio.Event(),self.memory.context()))
        while not controller.pending_questions: await asyncio.sleep(0)
        await controller.stop()
        observed.append(await first)
        observed.append(await self.run_turn('second',asyncio.Event(),self.memory.context()))
    monkeypatch.setattr(cli.VoiceSession,'run',run)
    await cli._listen_async(True)
    assert observed[0].startswith('Stopped.')
    assert observed[1]=='answer second'
