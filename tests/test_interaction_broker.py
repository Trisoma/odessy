import asyncio
import pytest


def test_broker_channel_readiness_atomic_resolution_and_tombstones():
    from odessy.interaction.broker import InteractionBroker, InteractionError
    async def run():
        broker = InteractionBroker(lambda *args: None, lambda value: value)
        broker.start_turn('turn')
        ready = asyncio.Event()
        cancelled = asyncio.Event()
        async def handler(entry):
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        broker.handler = handler
        task = asyncio.create_task(broker.request_approval({'risk': 'medium', 'prompt': 'Write file'}))
        await ready.wait()
        entry = broker.pending_approvals[0]
        with pytest.raises(InteractionError):
            broker.resolve_approval(entry['id'], True, channel='voice')
        broker.mark_ready(entry['id'])
        broker.resolve_approval(entry['id'], True, channel='authenticated_ui')
        with pytest.raises(InteractionError) as error:
            broker.resolve_approval(entry['id'], True, channel='terminal')
        assert error.value.status_code == 409
        assert await task is True
        assert cancelled.is_set()
        assert not broker.pending
        task = asyncio.create_task(broker.request_approval({'risk': 'high'}))
        await asyncio.sleep(0)
        entry = broker.pending_approvals[0]
        broker.mark_ready(entry['id'])
        with pytest.raises(InteractionError):
            broker.resolve_approval(entry['id'], True, channel='voice')
        broker.end_turn()
        assert await task is False
        with pytest.raises(InteractionError) as error:
            broker.resolve_approval(entry['id'], True, channel='terminal')
        assert error.value.status_code == 410
    asyncio.run(run())


def test_questions_validate_choices_limit_and_expire_without_blocking():
    from odessy.interaction.broker import InteractionBroker, InteractionError
    async def run():
        broker = InteractionBroker(lambda *args: None, lambda value: value,
                                   {'response_timeout': .01, 'max_questions': 1})
        broker.start_turn()
        task = asyncio.create_task(broker.ask('Choose', ['A', 'B'], False))
        await asyncio.sleep(0)
        qid = broker.pending_questions[0]['id']
        with pytest.raises(InteractionError):
            broker.resolve_question(qid, 'C', channel='authenticated_ui')
        assert broker.is_pending(qid)
        broker.resolve_question(qid, 'B', channel='authenticated_ui')
        result = await task
        assert result['answer'] == 'B' and result['source'] == 'human_information'
        assert (await broker.ask('Again'))['status'] == 'limit_reached'
        broker.start_turn()
        result = await broker.ask('Timeout')
        assert result['status'] == 'expired'
        assert not broker.pending
    asyncio.run(run())


def test_session_question_scope_and_stop():
    from odessy.application.session import SessionController
    from odessy.interaction.context import current_interactions
    from odessy.runtime.types import AgentTurnResult
    async def run():
        class Agent:
            async def run_turn(self, text, **kwargs):
                broker = current_interactions()
                assert broker is session.interactions
                result = await broker.ask('Which destination?')
                return AgentTurnResult(result['answer'], [])
        session = SessionController(model_factory=lambda _: object(),
            agent_factory=lambda *args, **kwargs: Agent(), profiles={'local': {}})
        task = asyncio.create_task(session.run_turn('help'))
        await asyncio.sleep(0)
        qid = session.pending_questions[0]['id']
        session.answer_question(qid, 'home')
        assert (await task).final_text == 'home'
        assert not session.pending_questions
        await session.close()
    asyncio.run(run())


def test_handler_failure_fails_closed_and_records_transport():
    from odessy.interaction.broker import InteractionBroker
    async def run():
        events = []
        broker = InteractionBroker(lambda kind, data: events.append((kind, data)), lambda value: value)
        async def broken(entry):
            raise RuntimeError('microphone disconnected')
        broker.handler = broken
        # A failed adapter must release the foreground operation immediately.
        result = await asyncio.wait_for(broker.ask('Continue?'), .2)
        assert result['status'] == 'cancelled'
        assert result['reason'] == 'handler_failed'
        assert result['error_type'] == 'RuntimeError'
        assert result['error'] == 'microphone disconnected'
        assert any(kind == 'interaction_unavailable' for kind, _ in events)
        broker.handler = None
        task = asyncio.create_task(broker.request_approval({'risk': 'medium'}))
        await asyncio.sleep(0)
        broker.resolve_approval(broker.pending[0]['id'], False, channel='terminal')
        assert await task is False
        answered = [data for kind, data in events if kind == 'approval_answered'][-1]
        assert answered['channel'] == 'terminal'
        assert answered['approved'] is False
    asyncio.run(run())


def test_prompt_phase_is_bounded_and_readiness_only_extends_once():
    from odessy.interaction.broker import InteractionBroker
    async def run():
        broker = InteractionBroker(lambda *args: None, lambda value: value,
                                   {'prompt_timeout': .01, 'response_timeout': .1})
        async def silent(entry):
            await asyncio.Event().wait()
        broker.handler = silent
        assert (await broker.ask('Never played'))['status'] == 'expired'
        task = asyncio.create_task(broker.ask('Played'))
        await asyncio.sleep(0)
        qid = broker.pending[0]['id']
        broker.mark_ready(qid)
        expiry = broker.pending[0]['expires_at']
        broker.mark_ready(qid)
        assert broker.pending[0]['expires_at'] == expiry
        broker.resolve_question(qid, 'answer', channel='voice')
        assert (await task)['answer'] == 'answer'
    asyncio.run(run())


def test_broker_explicit_deadline_excludes_human_wait_in_independent_context(monkeypatch):
    from types import SimpleNamespace
    import time
    from odessy.interaction.broker import InteractionBroker
    async def run():
        broker = InteractionBroker(lambda *args: None, lambda value: value)
        from odessy.interaction import context
        clock = [100.0]
        monkeypatch.setattr(context, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
        deadline = SimpleNamespace(_started=100.0)
        broker.generation_deadline = deadline
        started = deadline._started
        task = asyncio.create_task(broker.ask('Where?'))
        await asyncio.sleep(0)
        clock[0] += 10
        broker.resolve_question(broker.pending[0]['id'], 'home', channel='terminal')
        assert (await task)['answer'] == 'home'
        assert deadline._started - started == 10
    asyncio.run(run())


def test_unknown_risk_and_escalation_scope_cannot_enable_voice():
    from odessy.interaction.broker import InteractionBroker
    async def run():
        broker = InteractionBroker(lambda *args: None, lambda value: value)
        for details in ({'risk': 'unrecognized'}, {'risk': 'medium', 'scopes': 'escalation'}):
            task = asyncio.create_task(broker.request_approval(details))
            await asyncio.sleep(0)
            assert 'voice' not in broker.pending[0]['allowed_channels']
            broker.cancel_pending()
            assert await task is False
    asyncio.run(run())


def test_environment_interaction_bounds_are_configurable_and_validated(monkeypatch):
    from odessy.interaction.broker import InteractionBroker
    monkeypatch.setenv('ODESSY_INTERACTION_MAX_QUESTIONS', '2')
    monkeypatch.setenv('ODESSY_INTERACTION_RESPONSE_TIMEOUT', '15')
    broker = InteractionBroker(lambda *args: None, lambda value: value)
    assert broker.max_questions == 2 and broker.response_timeout == 15
    for value in ('0', '-1', 'nan', 'inf', '1000000', 'invalid'):
        monkeypatch.setenv('ODESSY_INTERACTION_RESPONSE_TIMEOUT', value)
        with pytest.raises(ValueError):
            InteractionBroker(lambda *args: None, lambda value: value)



def test_simultaneous_voice_and_ui_answers_have_one_winner():
    from odessy.interaction.broker import InteractionBroker, InteractionError
    async def run():
        broker = InteractionBroker(lambda *args: None, lambda value: value)
        task = asyncio.create_task(broker.ask('Destination?'))
        await asyncio.sleep(0)
        qid = broker.pending[0]['id']
        gate = asyncio.Event()
        async def answer(value, channel):
            await gate.wait()
            try:
                broker.resolve_question(qid, value, channel=channel)
                return 'won'
            except InteractionError as exc:
                return exc.status_code
        contenders = [asyncio.create_task(answer('voice destination', 'voice')),
                      asyncio.create_task(answer('UI destination', 'authenticated_ui'))]
        gate.set()
        outcomes = await asyncio.gather(*contenders)
        assert outcomes.count('won') == 1 and outcomes.count(409) == 1
        assert (await task)['answer'] in {'voice destination', 'UI destination'}
    asyncio.run(run())
