"""Composition checks: actual audio adapter, controller and terminal arbiter."""
import asyncio

import pytest

from odessy.runtime.types import AgentTurnResult
from test_hybrid_console import fixture_console
from test_hybrid_audio import build, say, record, wait_until, FakeTTS, FakePlayer


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def connect(tmp_path, callback=None, **audio_options):
    console, controller, _, output, seen = fixture_console(callback)
    rig = build(tmp_path, **audio_options)
    audio = console.audio = rig.adapter
    audio.submit_voice = console.submit_voice
    audio.stop_turn = console.stop_turn
    audio.memory = controller.memory
    audio.interactions = controller.interactions
    audio.report_audio_error = console.report_audio_error
    audio.report_status = console.display
    return console, controller, rig, output, seen


@pytest.mark.anyio
async def test_real_audio_and_keyboard_share_controller_history(tmp_path):
    console, controller, rig, output, seen = connect(tmp_path)
    try:
        assert await rig.adapter.start()
        await say(rig)
        await wait_until(lambda: len(controller.memory.turns) == 1)
        await console.wait_idle()
        assert await console.submit_text('typed followup')
        await console.wait_idle()
        assert len(controller.memory.turns) == 2
        assert len(seen[1][1].turns) == 1
        assert output.getvalue().count('Odessy: answer what time is it') == 1
        assert output.getvalue().count('Odessy: answer typed followup') == 1
    finally:
        await console.aclose()
        await controller.close()


@pytest.mark.anyio
async def test_one_shot_records_without_wake_and_closes_after_reply(tmp_path):
    console, controller, rig, output, seen = connect(tmp_path)
    console.persistent = False
    rig.adapter.listen = False
    rig.adapter.on_reply_complete = console.voice_reply_complete
    try:
        assert await rig.adapter.start()
        await record(rig)
        await wait_until(lambda: console._closing.is_set())
        assert len(seen) == 1
        assert rig.tts.texts == ['answer what time is it']
        assert len(rig.player.played) >= 2, 'ready cue and final reply both played'
        assert len(controller.memory.turns) == 1
    finally:
        await console.aclose()
        await controller.close()


@pytest.mark.anyio
async def test_capture_failure_keeps_pending_question_for_keyboard(tmp_path):
    console, controller, rig, output, seen = connect(tmp_path)
    try:
        assert await rig.adapter.start()
        pending = asyncio.create_task(controller.interactions.ask('Which?', ['A', 'B'], False))
        await wait_until(lambda: rig.adapter._session._interaction is not None)
        identifier = controller.pending_questions[0]['id']
        expiry = controller.pending_questions[0]['expires_at']
        rig.stream.fail_after = rig.stream.reads
        await wait_until(lambda: not rig.adapter.input_available)
        await asyncio.sleep(.03)
        assert controller.interactions.is_pending(identifier)
        assert controller.pending_questions[0]['expires_at'] == expiry
        await console.handle_line('B')
        assert (await pending)['answer'] == 'B'
        assert seen == []
    finally:
        await console.aclose()
        await controller.close()


@pytest.mark.anyio
async def test_capture_failure_during_model_work_never_replays_or_cancels_it(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    effects = []
    async def action(text, **kwargs):
        effects.append('tool effect')
        entered.set()
        await release.wait()
        return AgentTurnResult(final_text='actual completed result', log=[])
    console, controller, rig, output, seen = connect(tmp_path, action)
    try:
        assert await rig.adapter.start()
        await say(rig)
        await asyncio.wait_for(entered.wait(), 5)
        rig.stream.fail_after = rig.stream.reads
        await wait_until(lambda: not rig.adapter.input_available)
        assert controller.running
        assert await rig.adapter.set_input_enabled(True) is False
        assert controller.running
        release.set()
        await console.wait_idle()
        assert effects == ['tool effect']
        assert len(seen) == 1
        assert len(controller.memory.turns) == 1
        assert output.getvalue().count('Odessy: actual completed result') == 1
        assert await console.submit_text('next typed request')
        await console.wait_idle()
        assert len(seen) == 2
    finally:
        release.set()
        await console.aclose()
        await controller.close()


@pytest.mark.anyio
@pytest.mark.parametrize('failure', ['synthesis', 'playback'])
async def test_output_failure_keeps_final_result_and_single_execution(tmp_path, failure):
    class AnswerPlayer(FakePlayer):
        def play(self, path, **kwargs):
            if 'reply' in str(path):
                self.fail = 'speaker lost'
            return super().play(path, **kwargs)
    options = {'tts': FakeTTS(status='error')} if failure == 'synthesis' else {'player': AnswerPlayer()}
    console, controller, rig, output, seen = connect(tmp_path, **options)
    try:
        assert await rig.adapter.start()
        await say(rig)
        await wait_until(lambda: not rig.adapter.output_available)
        assert len(seen) == 1
        assert len(controller.memory.turns) == 1
        assert output.getvalue().count('Odessy: answer what time is it') == 1
        assert await console.submit_text('continue')
        await console.wait_idle()
        assert len(seen) == 2
    finally:
        await console.aclose()
        await controller.close()
