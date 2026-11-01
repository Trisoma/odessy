"""Accept-tap LAN pairing: the remote request, the local approval, and refusal.

The pairing routes run before a session exists and before the caller holds a
token, so they are exercised here without a controller or a model.
"""
import asyncio
import sys

import pytest

pytest.importorskip('fastapi')
httpx = pytest.importorskip('httpx')

from odessy.application import execution_access, pairing
from odessy.application.api import create_app

REMOTE = ('192.168.1.50', 51234)
LOCAL = ('127.0.0.1', 51234)
HOSTED_SECRET = 'exec-session-key'
WORKSPACE = 'C:/work/odessy'


@pytest.fixture(autouse=True)
def _isolated_grants():
    execution_access.reset()
    yield
    execution_access.reset()


def build_app(token='secret'):
    def factory():  # Pairing must never need a session or a model.
        raise AssertionError('pairing must not build a session controller')
    app = create_app(factory, token=token, workspace=WORKSPACE)
    app.state.pairing_port = 8765
    app.state.execution_url = 'http://192.168.1.10:9000/mcp'
    execution_access.configure(HOSTED_SECRET, WORKSPACE)
    probe = asyncio.Queue(maxsize=64)
    app.state.pairing_subscribers.add(probe)
    app.state.probe = probe
    return app


def drain(app):
    queue = app.state.probe
    return [queue.get_nowait() for _ in range(queue.qsize())]


def test_request_accepted(monkeypatch):
    async def run():
        app = build_app()
        seen = {}

        async def wait_for_approval(request_id, name, ip, timeout=60):
            seen.update(id=request_id, name=name, ip=ip)
            return True

        monkeypatch.setattr(pairing, 'wait_for_approval', wait_for_approval)
        monkeypatch.setattr(pairing, 'lan_ip', lambda: '192.168.1.10')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=REMOTE),
                                     base_url='http://test') as client:
            response = await client.post('/v1/pairing/request', json={'name': 'Jetson'})
        assert response.status_code == 200, response.text
        payload = response.json()
        # The coordinator gets a reviewable tool credential, never the app token.
        assert set(payload) == {'url', 'credential', 'workspace'}
        assert payload['url'] == 'http://192.168.1.10:9000/mcp'
        assert payload['workspace'] == WORKSPACE
        assert payload['credential'] == HOSTED_SECRET
        assert 'secret' not in response.text
        assert execution_access.validate(payload['credential'], workspace=WORKSPACE)
        assert not execution_access.validate(payload['credential'], workspace='C:/other')
        assert seen == {'id': seen['id'], 'name': 'Jetson', 'ip': '192.168.1.50'}
        assert len(seen['id']) == 16 and int(seen['id'], 16) >= 0
        assert pairing._pending == {}
        events = drain(app)
        assert [event['type'] for event in events] == ['pairing_request', 'pairing_resolved']
        assert events[0]['data'] == {'id': seen['id'], 'name': 'Jetson', 'ip': '192.168.1.50'}
        assert events[1]['data'] == {'id': seen['id'], 'accepted': True}

    asyncio.run(run())


def test_request_declined(monkeypatch):
    async def run():
        app = build_app()
        asked = []

        async def wait_for_approval(request_id, name, ip, timeout=60):
            asked.append((name, ip))
            return False

        monkeypatch.setattr(pairing, 'wait_for_approval', wait_for_approval)
        monkeypatch.setattr(pairing, 'lan_ip', lambda: '192.168.1.10')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=REMOTE),
                                     base_url='http://test') as client:
            response = await client.post('/v1/pairing/request', json={'name': 'Jetson'})
        assert response.status_code == 403
        assert response.json()['detail'] == 'Pairing declined'
        assert asked == [('Jetson', '192.168.1.50')]
        assert 'secret' not in response.text
        assert pairing._pending == {}
        assert [event['data'].get('accepted') for event in drain(app)] == [None, False]

    asyncio.run(run())


def test_approve_resolves():
    async def run():
        app = build_app()
        future = asyncio.get_running_loop().create_future()
        pairing._pending['0123456789abcdef'] = future
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=REMOTE),
                                         base_url='http://test') as client:
                body = {'id': '0123456789abcdef', 'accepted': True}
                anonymous = await client.post('/v1/pairing/approve', json=body)
                assert anonymous.status_code == 401
                assert not future.done()
                response = await client.post('/v1/pairing/approve', json=body,
                                             headers={'Authorization': 'Bearer secret'})
                assert response.status_code == 200, response.text
                assert response.json()['id'] == '0123456789abcdef'
                assert response.json()['accepted'] is True
                await asyncio.sleep(0)
                assert future.done() and future.result() is True
                unknown = await client.post('/v1/pairing/approve',
                                            json={'id': 'ffffffffffffffff', 'accepted': False},
                                            headers={'Authorization': 'Bearer secret'})
                assert unknown.status_code == 404
        finally:
            pairing._pending.clear()

    asyncio.run(run())


def test_request_is_released_by_a_tap(monkeypatch):
    """The real wait/resolve pair, not a stub: a tap must release the POST."""
    async def run():
        app = build_app()
        monkeypatch.setattr(pairing, 'lan_ip', lambda: '192.168.1.10')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=REMOTE),
                                     base_url='http://test') as client:
            pending = asyncio.create_task(client.post('/v1/pairing/request', json={'name': 'Jetson'}))
            for _ in range(200):
                if pairing._pending:
                    break
                await asyncio.sleep(0.005)
            assert pairing._pending, 'the request never reached the approval wait'
            request_id = next(iter(pairing._pending))
            tap = await client.post('/v1/pairing/approve', json={'id': request_id, 'accepted': True},
                                    headers={'Authorization': 'Bearer secret'})
            assert tap.status_code == 200, tap.text
            response = await asyncio.wait_for(pending, timeout=5)
            assert response.status_code == 200, response.text
            assert response.json()['url'] == 'http://192.168.1.10:9000/mcp'
        assert pairing._pending == {}

    asyncio.run(run())


def test_local_or_unreachable_hosts_cannot_pair(monkeypatch):
    async def run():
        app = build_app()
        monkeypatch.setattr(pairing, 'lan_ip', lambda: '192.168.1.10')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=LOCAL),
                                     base_url='http://test') as client:
            assert (await client.post('/v1/pairing/request', json={'name': 'Jetson'})).status_code == 403
            for name in ('', '   ', 'x' * 65, 7, None):
                assert (await client.post('/v1/pairing/request', json={'name': name})).status_code == 400
            assert (await client.post('/v1/pairing/request', json={'name': 'Jetson', 'port': 1})).status_code == 400
        monkeypatch.setattr(pairing, 'lan_ip', lambda: None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=REMOTE),
                                     base_url='http://test') as client:
            response = await client.post('/v1/pairing/request', json={'name': 'Jetson'})
            assert response.status_code == 503
        assert drain(app) == [], 'a refused request must not prompt the local user'
        assert pairing._pending == {}

    asyncio.run(run())


def test_an_execution_credential_opens_no_application_route(monkeypatch):
    """The tool secret must not become a second admin key."""
    async def run():
        app = build_app()

        async def accepted(request_id, name, ip, timeout=60):
            return True

        monkeypatch.setattr(pairing, 'wait_for_approval', accepted)
        monkeypatch.setattr(pairing, 'lan_ip', lambda: '192.168.1.10')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=REMOTE),
                                     base_url='http://test') as client:
            granted = await client.post('/v1/pairing/request', json={'name': 'Jetson'})
            assert granted.status_code == 200, granted.text
            credential = granted.json()['credential']
            headers = {'Authorization': 'Bearer ' + credential}
            for path in ('/v1/settings', '/v1/profiles', '/v1/capabilities', '/v1/sessions'):
                assert (await client.get(path, headers=headers)).status_code == 401, path
            assert (await client.post('/v1/sessions', headers=headers)).status_code == 401
            assert (await client.post('/v1/pairing/approve',
                                       json={'id': 'x', 'accepted': True},
                                       headers=headers)).status_code == 401

    asyncio.run(run())


def test_pairing_refuses_when_tool_hosting_is_not_enabled(monkeypatch):
    async def run():
        app = build_app()
        app.state.execution_url = None
        monkeypatch.setattr(pairing, 'lan_ip', lambda: '192.168.1.10')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=REMOTE),
                                     base_url='http://test') as client:
            response = await client.post('/v1/pairing/request', json={'name': 'Jetson'})
        assert response.status_code == 503
        assert 'LAN tool hosting is not enabled' in response.json()['detail']
        assert drain(app) == [], 'the local user must not be prompted for an unusable pairing'

    asyncio.run(run())


def test_wait_for_approval_times_out_without_leaving_a_pending_entry():
    async def run():
        accepted = await pairing.wait_for_approval('abcdefabcdefabcd', 'Jetson', '192.168.1.50', timeout=0.01)
        assert accepted is False
        assert 'abcdefabcdefabcd' not in pairing._pending
        assert pairing.resolve('abcdefabcdefabcd', True) is False

    asyncio.run(run())


def test_announcement_is_optional_but_the_port_is_always_recorded(monkeypatch):
    app = build_app()
    app.state.pairing_port = None
    monkeypatch.setattr(pairing, 'lan_ip', lambda: None)
    assert pairing.register_mdns(app, 8765) is None  # No LAN address to advertise.
    assert app.state.pairing_port == 8765
    monkeypatch.setattr(pairing, 'lan_ip', lambda: '192.168.1.10')
    monkeypatch.setitem(sys.modules, 'zeroconf', None)  # Simulates the extra not installed.
    assert pairing.register_mdns(app, 8765) is None
    assert not hasattr(app.state, 'zeroconf')


def test_unanswered_prompts_are_capped(monkeypatch):
    async def run():
        app = build_app()
        monkeypatch.setattr(pairing, 'lan_ip', lambda: '192.168.1.10')
        loop = asyncio.get_running_loop()
        pairing._pending.update({f'{index:016x}': loop.create_future()
                                 for index in range(pairing.MAX_PENDING)})
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=REMOTE),
                                         base_url='http://test') as client:
                response = await client.post('/v1/pairing/request', json={'name': 'Jetson'})
            assert response.status_code == 429
            assert drain(app) == [], 'the local user must not be prompted past the cap'
            assert len(pairing._pending) == pairing.MAX_PENDING
        finally:
            pairing._pending.clear()

    asyncio.run(run())
