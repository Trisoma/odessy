"""Authenticated, session-bound desktop voice route regressions."""
import asyncio

import httpx

from odessy.application.api import create_app
from odessy.application.desktop_voice import DesktopVoice
from odessy.application.session import SessionController


class Audio:
    async def start(self):
        pass

    async def cancel(self):
        pass

    async def close(self):
        pass


def session():
    return SessionController(model_factory=lambda _: object(),
                             agent_factory=lambda *args, **kwargs: object(),
                             profiles={'local': {}})


def test_voice_routes_require_authentication_and_bind_capture_to_session():
    async def run():
        voice = DesktopVoice(Audio, host='local-test')
        app = create_app(session, token='fixture-token', desktop_voice=voice)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            assert (await client.get('/v1/sessions/example/voice')).status_code == 401
            client.headers['Authorization'] = 'Bearer fixture-token'
            first = (await client.post('/v1/sessions')).json()['id']
            second = (await client.post('/v1/sessions')).json()['id']
            path = f'/v1/sessions/{first}/voice'
            capture = await client.post(path, json={'action': 'start_capture'})
            assert capture.status_code == 200
            capture_id = capture.json()['capture_id']
            assert capture_id
            denied = await client.post(f'/v1/sessions/{second}/voice',
                                       json={'action': 'start_capture'})
            assert denied.status_code == 409
            stale = await client.post(path, json={'action': 'cancel_capture',
                                                  'capture_id': 'wrong'})
            assert stale.status_code == 410
            cancelled = await client.post(path, json={'action': 'cancel_capture',
                                                      'capture_id': capture_id})
            assert cancelled.status_code == 200
            assert cancelled.json()['state'] == 'idle'
            await client.delete(f'/v1/sessions/{first}')
            await client.delete(f'/v1/sessions/{second}')
        await voice.close()

    asyncio.run(run())
