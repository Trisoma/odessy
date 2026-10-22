import asyncio
import io
from types import SimpleNamespace

import pytest

from odessy.application.session import SessionController
from odessy.runtime.types import AgentTurnResult
from odessy.application.hybrid import HybridConsole, ForegroundBusy


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Tty(io.StringIO):
    def isatty(self):
        return True


class Audio:
    input_available = True
    output_available = True

    def __init__(self):
        self.typed = []
        self.spoken = []
        self.closed = False

    async def start(self):
        return self.input_available

    async def set_typed_turn(self, active):
        self.typed.append(active)

    async def set_input_enabled(self, enabled):
        self.input_available = enabled
        return enabled

    async def set_output_enabled(self, enabled):
        self.output_available = enabled

    async def speak(self, text):
        self.spoken.append(text)

    def stop_speaking(self):
        pass

    async def handle_interaction(self, entry):
        await asyncio.Future()

    async def aclose(self):
        self.closed = True


def fixture_console(callback=None, **options):
    seen = []
    class Agent:
        async def run_turn(self, text, **kwargs):
            seen.append((text, kwargs['context']))
            if callback:
                return await callback(text, **kwargs)
            return AgentTurnResult(final_text='answer ' + text, log=[])
    controller = SessionController(model_factory=lambda name: object(),
        agent_factory=lambda *a, **k: Agent(), profiles={'current': {}, 'other': {}},
        config=SimpleNamespace(participants={}))
    output, audio = io.StringIO(), Audio()
    console = HybridConsole(controller, audio=audio, stdin=Tty(), stdout=output, **options)
    return console, controller, audio, output, seen


@pytest.mark.anyio
async def test_stop_before_controller_start_does_not_dispatch_pending_request():
    console, controller, audio, output, seen = fixture_console()
    assert await console.submit_text('must not run')
    await console.stop_turn()
    assert seen == []
    assert not controller.memory.turns
    await console.aclose()
    await controller.close()


@pytest.mark.anyio
async def test_close_before_controller_start_does_not_dispatch_pending_request():
    console, controller, audio, output, seen = fixture_console()
    assert await console.submit_text('must not run')
    await console.aclose()
    assert seen == []
    assert audio.closed
    await controller.close()


@pytest.mark.anyio
async def test_stop_during_audio_preparation_prevents_model_dispatch():
    console, controller, audio, output, seen = fixture_console()
    entered, release = asyncio.Event(), asyncio.Event()
    async def prepare(active):
        if active:
            entered.set()
            await release.wait()
    audio.set_typed_turn = prepare
    assert await console.submit_text('must not run')
    await entered.wait()
    stop = asyncio.create_task(console.stop_turn())
    await asyncio.sleep(0)
    release.set()
    await stop
    assert seen == []
    await console.aclose()
    await controller.close()


@pytest.mark.anyio
async def test_voice_and_text_share_history_once_and_retain_selection():
    console, controller, audio, output, seen = fixture_console()
    controller.select_model('other')
    await console.submit_voice('voice question', asyncio.Event(), None)
    assert await console.submit_text('typed followup')
    await console.wait_idle()
    assert await console.submit_text('password=123456789')
    await console.wait_idle()
    await console.submit_voice('voice followup', asyncio.Event(), None)
    assert len(controller.memory.turns) == 4
    assert len(seen[1][1].turns) == 1
    assert len(seen[-1][1].turns) == 3
    assert '123456789' not in str(controller.memory.turns)
    assert controller.selected_profile == 'other'
    assert output.getvalue().count('Odessy: answer voice question') == 1
    assert not audio.spoken, 'the voice adapter owns voice speech; typed turns are display only'
    await console.aclose()
    await controller.close()


@pytest.mark.anyio
async def test_admission_is_atomic_and_busy_requests_are_not_queued():
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow(text, **kwargs):
        entered.set()
        await release.wait()
        return AgentTurnResult(final_text='done', log=[])
    console, controller, audio, output, seen = fixture_console(slow)
    assert await console.submit_text('first')
    assert not await console.submit_text('second')
    with pytest.raises(ForegroundBusy):
        await console.submit_voice('third', asyncio.Event(), None)
    await entered.wait()
    release.set()
    await console.wait_idle()
    assert [item[0] for item in seen] == ['first']
    assert audio.typed == [True, False]
    assert 'busy' in output.getvalue().lower()
    await console.aclose()
    await controller.close()


@pytest.mark.anyio
async def test_pending_approval_uses_exact_phrase_and_trusted_terminal_channel():
    console, controller, audio, output, seen = fixture_console()
    task = asyncio.create_task(controller.interactions.request_approval({
        'risk': 'high', 'prompt': 'Modify the validated file', 'scopes': ['write'],
        'arguments': {'path': '/validated'}, 'operation': 'write', 'host': 'local'}))
    await asyncio.sleep(.01)
    identifier = controller.pending_approvals[0]['id']
    await console.handle_line('yes')
    assert not task.done()
    assert '/validated' in output.getvalue()
    await console.handle_line('approve this action')
    assert await task is True
    assert not seen
    assert controller.interactions._tombstones[identifier]['channel'] == 'terminal'
    await console.aclose()
    await controller.close()


@pytest.mark.anyio
async def test_pretyped_reply_cannot_authorize_a_new_prompt():
    console, controller, audio, output, seen = fixture_console()
    generation = console.input_generation
    task = asyncio.create_task(controller.interactions.request_approval({'risk': 'medium'}))
    await asyncio.sleep(.01)
    await console.handle_line('approve this action', generation=generation)
    assert not task.done()
    await console.handle_line('deny')
    assert await task is False
    await console.aclose()
    await controller.close()


@pytest.mark.anyio
async def test_question_choices_and_api_resolution_do_not_become_new_requests():
    console, controller, audio, output, seen = fixture_console()
    task = asyncio.create_task(controller.interactions.ask('Which?', ['A', 'B'], False))
    await asyncio.sleep(.01)
    identifier = controller.pending_questions[0]['id']
    generation = console.input_generation
    await console.handle_line('C')
    assert not task.done()
    controller.answer_question(identifier, 'B')
    result = await task
    assert result['answer'] == 'B'
    await asyncio.sleep(0)
    await console.handle_line('A', generation=generation)
    assert not seen
    await console.aclose()
    await controller.close()


@pytest.mark.anyio
async def test_text_voice_switches_do_not_stop_an_active_turn():
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow(text, **kwargs):
        entered.set()
        await release.wait()
        assert not kwargs['stop_event'].is_set()
        return AgentTurnResult(final_text='done', log=[])
    console, controller, audio, output, seen = fixture_console(slow)
    await console.submit_text('work')
    await entered.wait()
    await console.handle_line('/text')
    assert not audio.input_available and not audio.output_available
    await console.handle_line('/voice')
    assert audio.input_available and audio.output_available
    release.set()
    await console.wait_idle()
    assert len(seen) == 1
    await console.aclose()
    await controller.close()


@pytest.mark.anyio
async def test_unknown_commands_never_reach_the_model():
    console, controller, audio, output, seen = fixture_console()
    await console.handle_line('/unknown')
    assert not seen
    assert '/help' in output.getvalue()
    await console.aclose()
    await controller.close()


@pytest.mark.anyio
async def test_one_keyboard_reader_survives_audio_startup_failure_and_eof():
    queue = asyncio.Queue()
    reads = active = peak = 0
    async def read(stream, output):
        nonlocal reads, active, peak
        reads += 1
        active += 1
        peak = max(peak, active)
        try:
            return await queue.get()
        finally:
            active -= 1
    console, controller, audio, output, seen = fixture_console(read_line=read)
    audio.input_available = False
    runner = asyncio.create_task(console.run())
    await asyncio.sleep(.01)
    queue.put_nowait('hello')
    await asyncio.sleep(.03)
    queue.put_nowait(None)
    await asyncio.wait_for(runner, 1)
    assert [item[0] for item in seen] == ['hello']
    assert peak == 1 and active == 0 and audio.closed
    assert 'keyboard' in output.getvalue().lower()
    await controller.close()


@pytest.mark.anyio
async def test_stop_cancels_only_the_turn_and_keeps_session_usable():
    entered = asyncio.Event()
    async def slow(text, **kwargs):
        if text == 'slow':
            entered.set()
            await asyncio.Future()
        return AgentTurnResult(final_text='ok', log=[])
    console, controller, audio, output, seen = fixture_console(slow)
    await console.submit_text('slow')
    await entered.wait()
    await console.handle_line('/stop')
    await console.wait_idle()
    assert not controller.memory.turns
    assert await console.submit_text('next')
    await console.wait_idle()
    assert len(controller.memory.turns) == 1
    await console.aclose()
    await controller.close()
