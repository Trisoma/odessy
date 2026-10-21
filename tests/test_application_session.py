import asyncio
import pytest
from odessy.application.session import SessionController, parse_session_command
from odessy.runtime.types import AgentTurnResult

class Model:
    pass


def test_stopped_turn_does_not_decline_future_standalone_job_approval():
    async def run():
        from types import SimpleNamespace
        from odessy.runtime.types import ToolRequest
        controller = SessionController(model_factory=lambda name: Model(),
            agent_factory=lambda *args, **kwargs: None, profiles={"local": {}})
        await controller.stop()
        task = asyncio.create_task(controller.confirm(SimpleNamespace(reason="Delegate", risk="medium"),
            ToolRequest("agent.delegate", {"participant": "coder", "task": "Review"})))
        await asyncio.sleep(0)
        assert controller.pending_approvals
        controller.answer_approval(controller.pending_approvals[0]["id"], True)
        assert await task is True
        await controller.close()
    asyncio.run(run())


def test_default_cloud_credential_is_redacted_from_session_events(monkeypatch):
    secret = 'opaque-provider-credential'
    monkeypatch.setenv('OPENAI_API_KEY', secret)
    controller = SessionController(model_factory=lambda name: Model(),
        agent_factory=lambda *args, **kwargs: None,
        profiles={'cloud': {'adapter': 'cloud', 'provider': 'openai'}})
    controller.emit('test', {'answer': secret})
    assert secret not in str(controller.events)


def test_active_coordinator_key_stays_redacted_after_next_model_selection():
    async def run():
        from types import SimpleNamespace
        entered, resume = asyncio.Event(), asyncio.Event()
        secret = 'opaque-active-key'
        models = {'current': SimpleNamespace(api_key=secret), 'next': Model()}

        class SlowAgent:
            async def run_turn(self, text, **kwargs):
                entered.set()
                await resume.wait()
                kwargs['status_callback'](secret)
                return AgentTurnResult(secret, [])

        controller = SessionController(model_factory=models.__getitem__,
            agent_factory=lambda *args, **kwargs: SlowAgent(), profiles={name: {} for name in models})
        task = asyncio.create_task(controller.run_turn('hello'))
        await entered.wait()
        controller.select_model('next')
        resume.set()
        await task
        assert secret not in str(controller.events)
        assert secret not in str(controller.memory.turns)
        await controller.close()
    asyncio.run(run())

class Agent:
    def __init__(self, model, **kwargs):
        self.model, self.confirm = model, kwargs['confirm']
    async def run_turn(self, text, **kwargs):
        return AgentTurnResult(text + ':' + self.model.name, [])

def controller(**kwargs):
    def factory(name):
        model = Model()
        model.name = name
        return model
    return SessionController(model_factory=factory, agent_factory=Agent,
                             profiles={'local': {}, 'cloud': {}}, **kwargs)

def test_selection_preserves_shared_history_and_replay():
    async def run():
        session = controller(event_limit=6)
        assert (await session.run_turn('one')).final_text == 'one:local'
        session.select_model('cloud')
        assert (await session.run_turn('two')).final_text == 'two:cloud'
        assert [t.user for t in session.memory.turns] == ['one', 'two']
        assert len(session.events) <= 6
        assert [e['id'] for e in session.events] == sorted(e['id'] for e in session.events)
        with pytest.raises(ValueError):
            session.select_model('absent')
        await session.close()
    asyncio.run(run())

def test_active_turn_is_pinned_and_stop_declines_approval():
    async def run():
        ready = asyncio.Event()
        class Waiting(Agent):
            async def run_turn(self, text, **kwargs):
                ready.set()
                approved = await self.confirm({'reason': 'write'}, {'name': 'write'})
                return AgentTurnResult(str(approved) + self.model.name, [])
        session = controller()
        session.agent_factory = Waiting
        task = asyncio.create_task(session.run_turn('write'))
        await ready.wait()
        session.select_model('cloud')
        assert session.active_profile == 'local'
        with pytest.raises(RuntimeError):
            await session.run_turn('overlap')
        assert session.pending_approvals
        await session.stop()
        assert not session.pending_approvals
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert not session.memory.turns
    asyncio.run(run())

def test_commands_only_recognize_configured_standalone_aliases():
    assert parse_session_command('switch model to CLOUD', ['local', 'cloud']) == {'action':'switch_model','profile':'cloud'}
    assert parse_session_command('consult local: review this', ['local']) == {'action':'consult','participant':'local','question':'review this'}
    assert parse_session_command('please switch model to cloud', ['cloud']) is None
    assert parse_session_command('switch model to unknown', ['cloud']) is None

def test_explicit_consultation_is_attributed_and_history_stays_original():
    async def run():
        from odessy.runtime.types import AgentTurnMetrics
        calls = []
        class Service:
            def start_turn(self, text):
                calls.append(('start', text))
            def consult(self, **kwargs):
                calls.append(('consult', kwargs))
                return {'participant': 'peer', 'text': 'reference answer'}
        class Synthesizer(Agent):
            async def run_turn(self, text, **kwargs):
                observation = kwargs['initial_observation']
                assert text == 'original question'
                assert observation.result['participant'] == 'peer'
                assert not kwargs['context'].turns
                return AgentTurnResult('synthesis', [], metrics=AgentTurnMetrics(prompt_tokens=10))
        session = controller(collaboration=Service())
        session.agent_factory = Synthesizer
        await session.run_turn('original question', participant='peer')
        assert calls[0] == ('start', 'original question')
        assert calls[1][1]['explicit'] is True
        assert session.memory.turns[0].user == 'original question'
        usage = [event for event in session.events if event['type'] == 'usage'][0]
        assert usage['data']['prompt_tokens'] == 10
    asyncio.run(run())


def test_replay_then_live_events_has_no_duplicates_and_is_bounded():
    async def run():
        session = controller(event_limit=2, subscriber_limit=1)
        for n in range(3):
            session.emit('progress', {'n': n})
        stream = session.subscribe(0)
        assert (await anext(stream))['data']['n'] == 1
        assert (await anext(stream))['data']['n'] == 2
        session.emit('progress', {'n': 3})
        assert (await anext(stream))['data']['n'] == 3
        session.emit('progress', {'n': 4})
        session.emit('progress', {'n': 5})
        assert (await anext(stream))['data']['n'] == 5
        await stream.aclose()
        await session.close()
    asyncio.run(run())

def test_router_and_service_announcements_reach_events_from_worker_threads():
    async def run():
        announced = []
        class Announcing(Agent):
            async def run_turn(self, text, **kwargs):
                await asyncio.to_thread(self.model._announce, 'routed capable: conversation_context')
                return AgentTurnResult('done', [])
        session = controller()
        session._model._announce = announced.append
        session.agent_factory = Announcing
        await session.run_turn('question')
        assert announced == ['routed capable: conversation_context']
        assert any(event['type'] == 'routing' and 'capable' in event['data']['message'] for event in session.events)
    asyncio.run(run())

def test_known_profile_credentials_are_redacted_in_events_and_history(monkeypatch):
    async def run():
        from odessy.collaboration.config import ParticipantProfile
        monkeypatch.setenv('MY_MODEL_CREDENTIAL', 'opaque-sensitive-value')
        session = SessionController(model_factory=lambda name: Model(), agent_factory=Agent,
            profiles={'local': ParticipantProfile('local','cloud',credential_env='MY_MODEL_CREDENTIAL')})
        session._model.name = 'local'
        await session.run_turn('opaque-sensitive-value')
        assert 'opaque-sensitive-value' not in str(session.events)
        assert 'opaque-sensitive-value' not in str(session.memory.turns)
    asyncio.run(run())


def test_subscriber_count_is_bounded_and_close_wakes_waiters():
    async def run():
        session = controller(max_subscribers=1)
        stream = session.subscribe()
        waiter = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        other = session.subscribe()
        with pytest.raises(RuntimeError, match='subscriber'):
            await anext(other)
        await session.close()
        with pytest.raises(StopAsyncIteration):
            await waiter
        await stream.aclose()
    asyncio.run(run())

def test_external_voice_context_and_memory_owner_are_preserved():
    async def run():
        from odessy.runtime.types import ConversationContext, ConversationTurn
        external = ConversationContext((ConversationTurn('prior', 'reply'),))
        progress, observed = [], []
        stop_event = asyncio.Event()
        class ContextAgent(Agent):
            async def run_turn(self, text, **kwargs):
                observed.append(kwargs['context'])
                assert kwargs['stop_event'] is stop_event
                kwargs['status_callback']('working')
                return AgentTurnResult('reply', [])
        session = controller()
        session.agent_factory = ContextAgent
        await session.run_turn('next', context=external, stop_event=stop_event,
                               status_callback=progress.append, remember=False)
        assert observed == [external]
        assert progress == ['working']
        assert not session.memory.turns
    asyncio.run(run())


def test_usage_resets_each_turn_without_resetting_process_total():
    async def run():
        from odessy.models.usage import UsageLedger, reset_process_ledger
        ledger = UsageLedger()
        reset_process_ledger(ledger)
        class Metered(Agent):
            async def run_turn(self, text, **kwargs):
                ledger.record(prompt=10, cached=0, completion=2)
                return AgentTurnResult('reply', [])
        try:
            session = controller()
            session.agent_factory = Metered
            await session.run_turn('one')
            await session.run_turn('two')
            assert ledger.turn.prompt == 10
            assert ledger.total.prompt == 20
        finally:
            reset_process_ledger()
    asyncio.run(run())

def test_stop_drains_failed_consultation_without_leaking_worker_failure():
    async def run():
        import threading
        started, finish = threading.Event(), threading.Event()
        class Service:
            def start_turn(self, text):
                pass
            def consult(self, **kwargs):
                started.set()
                finish.wait(2)
                raise ValueError('provider failure during cancellation')
        session = controller(collaboration=Service())
        turn = asyncio.create_task(session.run_turn('question', participant='peer'))
        await asyncio.to_thread(started.wait, 1)
        stopper = asyncio.create_task(session.stop())
        await asyncio.sleep(0)
        finish.set()
        await stopper
        with pytest.raises(asyncio.CancelledError):
            await turn
        assert not session.running
    asyncio.run(run())
