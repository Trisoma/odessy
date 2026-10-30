"""Exercise desktop voice with inert fake audio; never opens a microphone."""
import asyncio
import httpx
from odessy.application.api import create_app
from odessy.application.desktop_voice import DesktopVoice
from odessy.application.session import SessionController
from odessy.runtime.types import AgentTurnResult


class FakeAudio:
    def __init__(self, controller, submit, report, *, succeeds=True):
        self.controller, self.submit, self.report = controller, submit, report
        self.succeeds = succeeds
        self.input_available = self.output_available = False
        self.started = self.closed = 0
        self.typed = []

    async def start(self):
        self.started += 1
        self.input_available = self.output_available = self.succeeds
        return self.succeeds

    async def set_typed_turn(self, active):
        self.typed.append(active)

    async def aclose(self):
        self.closed += 1
        self.input_available = self.output_available = False


class Agent:
    def __init__(self, model, **kwargs): pass
    async def run_turn(self, text, **kwargs):
        return AgentTurnResult('response', [])


def session():
    return SessionController(model_factory=lambda name: name, agent_factory=Agent,
                             profiles={'local': {}})


def test_voice_api_is_explicit_authenticated_and_session_scoped():
    async def run():
        adapters = []
        def factory(controller, submit, report):
            adapter = FakeAudio(controller, submit, report)
            adapters.append(adapter)
            return adapter
        app = create_app(session, token='fixture-token', voice_factory=factory)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.post('/v1/sessions/x/voice/start')).status_code == 401
            headers = {'Authorization': 'Bearer fixture-token'}
            a = (await client.post('/v1/sessions', headers=headers)).json()['id']
            b = (await client.post('/v1/sessions', headers=headers)).json()['id']
            assert not adapters
            route = f'/v1/sessions/{a}/voice'
            assert (await client.get(route, headers=headers)).json()['state'] == 'off'
            assert not adapters
            assert (await client.post(route + '/start', headers=headers)).status_code == 202
            await asyncio.sleep(0)
            assert adapters[0].started == 1
            assert (await client.get(route, headers=headers)).json()['state'] == 'active'
            assert (await client.post(route + '/heartbeat', headers=headers)).status_code == 200
            assert (await client.post(f'/v1/sessions/{b}/voice/start', headers=headers)).status_code == 409
            assert (await client.post(f'/v1/sessions/{b}/voice/stop', headers=headers)).json()['state'] == 'off'
            assert adapters[0].closed == 0
            assert (await client.post(route + '/stop', headers=headers)).json()['state'] == 'off'
            assert adapters[0].closed == 1
            assert (await client.post(route + '/heartbeat', headers=headers)).status_code == 409
            assert (await client.delete(f'/v1/sessions/{a}', headers=headers)).status_code == 204
            assert (await client.delete(f'/v1/sessions/{b}', headers=headers)).status_code == 204
    asyncio.run(run())


def test_lease_expires_and_audio_failure_is_not_reported_as_success():
    async def run():
        adapters = []
        def factory(controller, submit, report):
            result = FakeAudio(controller, submit, report)
            adapters.append(result)
            return result
        manager = DesktopVoice(factory=factory, lease_seconds=.03)
        c = session()
        await manager.start(c)
        await asyncio.sleep(.1)
        assert adapters[0].closed == 1
        assert manager.status(c)['state'] == 'off'
        failing = DesktopVoice(factory=lambda c, s, r: FakeAudio(c, s, r, succeeds=False))
        await failing.start(c)
        await asyncio.sleep(.01)
        assert failing.status(c)['state'] == 'unavailable'
        await failing.stop(c)
        assert failing.status(c)['state'] == 'off'
        await c.close()
    asyncio.run(run())


def test_typed_turn_temporarily_takes_foreground_and_voice_is_stopped_on_delete():
    async def run():
        adapters = []
        def factory(controller, submit, report):
            adapter = FakeAudio(controller, submit, report)
            adapters.append(adapter)
            return adapter
        app = create_app(session, token='fixture-token', voice_factory=factory)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer fixture-token'}) as client:
            sid = (await client.post('/v1/sessions')).json()['id']
            route = f'/v1/sessions/{sid}'
            await client.post(route + '/voice/start')
            await asyncio.sleep(0)
            assert (await client.post(route + '/turns', json={'text': 'hello'})).status_code == 202
            for _ in range(100):
                if adapters[0].typed == [True, False]: break
                await asyncio.sleep(.001)
            assert adapters[0].typed == [True, False]
            await client.delete(route)
            assert adapters[0].closed == 1
    asyncio.run(run())


def test_stop_during_startup_closes_resources_and_failed_close_is_not_reported_off():
    async def run():
        class PendingAudio(FakeAudio):
            async def start(self):
                self.started += 1
                await asyncio.Event().wait()
        pending = []
        def factory(c, s, r):
            result = PendingAudio(c, s, r)
            pending.append(result)
            return result
        c = session()
        manager = DesktopVoice(factory=factory)
        await manager.start(c)
        await asyncio.sleep(0)
        await manager.stop(c)
        assert pending[0].closed == 1
        assert manager.status(c)['state'] == 'off'

        class BrokenAudio(FakeAudio):
            async def aclose(self):
                raise RuntimeError('private device detail')
        broken = DesktopVoice(factory=lambda c, s, r: BrokenAudio(c, s, r))
        await broken.start(c)
        await asyncio.sleep(0)
        try:
            await broken.stop(c)
            assert False, 'shutdown must fail'
        except RuntimeError:
            pass
        assert broken.status(c)['state'] == 'stop_failed'
        assert 'private device detail' not in str(c.events)
        await c.close()
    asyncio.run(run())
