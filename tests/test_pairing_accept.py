"""LAN pairing: the remote request, the local decision, and refusal.

The accept-tap route set (an unauthenticated request route plus a long-polled
``approve`` and an SSE ``events`` stream on the application API) was retired in
78dc577. Approval is now the executor's own terminal prompt, and the executor's
listener serves exactly one unauthenticated route. These assertions hold the security
intent of the retired tests against that API: a decline hands out nothing, a local or
off-network caller cannot even prompt the local user, the credential opens no
application route, prompts are capped, and an expired prompt leaves no answer behind.
"""
import asyncio
import io
import json
import os
import sys
import urllib.error
import urllib.request

import pytest

from odessy.application import execution_access, pairing
from odessy.application.executor import HostSettings, TerminalApproval, create_listener

WORKSPACE = 'C:/work/odessy'
CREDENTIAL = 'execution-session-key'
MCP_URL = 'http://192.168.1.10:9000/mcp'
# A request driven over this machine's loopback arrives from 127.0.0.1, so LAN_ONLY
# deliberately excludes it.
LOOPBACK = ['127.0.0.0/8']
LAN_ONLY = ['192.168.0.0/16']


@pytest.fixture(autouse=True)
def _isolated_grants():
    execution_access.reset()
    yield
    execution_access.reset()


class Gate:
    """An approval that answers only when the test opens it."""

    def __init__(self):
        self.asked = []
        self.waiting = False
        self._future = None

    async def ask(self, name, workspace, *, timeout=60):
        self.asked.append((name, str(workspace)))
        self._future = asyncio.get_running_loop().create_future()
        self.waiting = True
        return await self._future

    def answer(self, accepted):
        self.waiting = False
        self._future.set_result(accepted)


def settings_for(tmp_path):
    return HostSettings.load(tmp_path / 'host.json', workspace=tmp_path / 'workspace')


def ask_body(settings, **overrides):
    return {'name': 'Jetson', 'device_id': settings.device_id, **overrides}


def post(port, payload, *, timeout=10):
    """One pairing request over a real socket; returns ``(status, body)``."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}/v1/pairing/request',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def test_a_request_is_answered_by_the_local_terminal_prompt(tmp_path):
    """The real prompt, not a stub: a 'y' at the console releases the POST."""
    async def run():
        settings = settings_for(tmp_path)
        output = io.StringIO()
        listener = create_listener(settings, TerminalApproval(io.StringIO('y\n'), output),
                                   CREDENTIAL, MCP_URL, host='127.0.0.1', networks=LOOPBACK)
        try:
            status, payload = await asyncio.to_thread(
                post, listener.server_address[1], ask_body(settings))
        finally:
            await asyncio.to_thread(listener.shutdown)
            listener.server_close()
        assert status == 200, payload
        # The coordinator gets the tool credential and nothing that opens this app.
        assert set(payload) == {'credential', 'url', 'workspace', 'device_id'}
        assert payload['credential'] == CREDENTIAL
        assert payload['url'] == MCP_URL
        assert payload['workspace'] == str(settings.workspace)
        assert payload['device_id'] == settings.device_id
        # The local user was actually asked, and told which workspace is at stake.
        assert 'Jetson' in output.getvalue()
        assert str(settings.workspace) in output.getvalue()

    asyncio.run(run())


def test_a_declined_request_returns_no_credential(tmp_path):
    async def run():
        settings = settings_for(tmp_path)
        output = io.StringIO()
        listener = create_listener(settings, TerminalApproval(io.StringIO('n\n'), output),
                                   CREDENTIAL, MCP_URL, host='127.0.0.1', networks=LOOPBACK)
        try:
            status, payload = await asyncio.to_thread(
                post, listener.server_address[1], ask_body(settings))
            assert not listener.accepted
        finally:
            await asyncio.to_thread(listener.shutdown)
            listener.server_close()
        assert status == 403
        assert payload['error'] == 'Connection declined or expired'
        assert 'credential' not in payload
        assert CREDENTIAL not in json.dumps(payload)
        # The user was asked: declining is not the same as never asking.
        assert 'requests access to' in output.getvalue()

    asyncio.run(run())


def test_a_second_coordinator_is_refused_once_one_is_connected(tmp_path):
    async def run():
        settings = settings_for(tmp_path)
        listener = create_listener(settings, TerminalApproval(io.StringIO('y\ny\n'), io.StringIO()),
                                   CREDENTIAL, MCP_URL, host='127.0.0.1', networks=LOOPBACK)
        try:
            first = await asyncio.to_thread(post, listener.server_address[1], ask_body(settings))
            second = await asyncio.to_thread(
                post, listener.server_address[1], ask_body(settings, name='Other'))
        finally:
            await asyncio.to_thread(listener.shutdown)
            listener.server_close()
        assert first[0] == 200, first[1]
        assert second[0] == 409
        assert second[1]['error'] == 'A coordinator is already connected. Restart sharing to change it.'

    asyncio.run(run())


def test_unanswered_prompts_are_capped_and_never_queued(tmp_path):
    """The unauthenticated route can put at most one question in front of the user."""
    async def run():
        settings = settings_for(tmp_path)
        gate = Gate()
        listener = create_listener(settings, gate, CREDENTIAL, MCP_URL,
                                   host='127.0.0.1', networks=LOOPBACK)
        try:
            first = asyncio.create_task(asyncio.to_thread(
                post, listener.server_address[1], ask_body(settings)))
            for _ in range(400):
                if gate.waiting:
                    break
                await asyncio.sleep(0.01)
            assert gate.waiting, 'the request never reached the approval prompt'
            second = await asyncio.to_thread(
                post, listener.server_address[1], ask_body(settings, name='Other'))
            assert second[0] == 409
            assert second[1]['error'] == 'Another coordinator or request is already active'
            assert len(gate.asked) == 1, 'the local user must not be prompted past the cap'
            gate.answer(False)
            assert (await first)[0] == 403
        finally:
            await asyncio.to_thread(listener.shutdown)
            listener.server_close()

    asyncio.run(run())


def test_a_peer_off_the_allowed_network_cannot_even_prompt_the_user(tmp_path):
    async def run():
        settings = settings_for(tmp_path)
        gate = Gate()
        listener = create_listener(settings, gate, CREDENTIAL, MCP_URL,
                                   host='127.0.0.1', networks=LAN_ONLY)
        try:
            status, payload = await asyncio.to_thread(
                post, listener.server_address[1], ask_body(settings))
        finally:
            await asyncio.to_thread(listener.shutdown)
            listener.server_close()
        assert status == 403
        assert payload['error'] == 'Use the same local network'
        assert gate.asked == [], 'a refused request must not reach the local user'

    asyncio.run(run())


def test_the_paired_credential_opens_no_application_route(tmp_path):
    """The tool secret must not become a second admin key."""
    pytest.importorskip('fastapi')
    httpx = pytest.importorskip('httpx')
    from odessy.application.api import create_app

    async def run():
        settings = settings_for(tmp_path)
        listener = create_listener(settings, TerminalApproval(io.StringIO('y\n'), io.StringIO()),
                                   CREDENTIAL, MCP_URL, host='127.0.0.1', networks=LOOPBACK)
        try:
            status, payload = await asyncio.to_thread(
                post, listener.server_address[1], ask_body(settings))
        finally:
            await asyncio.to_thread(listener.shutdown)
            listener.server_close()
        assert status == 200, payload

        def factory():  # An execution credential must never build a session controller.
            raise AssertionError('an execution credential must not build a controller')

        app = create_app(factory, token='admin-app-token')
        headers = {'Authorization': 'Bearer ' + payload['credential']}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            for path in ('/v1/settings', '/v1/profiles', '/v1/capabilities', '/v1/sessions'):
                assert (await client.get(path, headers=headers)).status_code == 401, path
            assert (await client.post('/v1/sessions', headers=headers)).status_code == 401
            # The retired approval route is not an alternative authority either.
            assert (await client.post('/v1/pairing/approve',
                                       json={'id': 'x', 'accepted': True},
                                       headers=headers)).status_code == 401

    asyncio.run(run())


def test_a_malformed_or_stale_request_is_refused_before_any_prompt(tmp_path):
    async def run():
        settings = settings_for(tmp_path)
        gate = Gate()
        listener = create_listener(settings, gate, CREDENTIAL, MCP_URL,
                                   host='127.0.0.1', networks=LOOPBACK)
        port = listener.server_address[1]
        expected = [
            ({'name': '', 'device_id': settings.device_id}, 400, 'Invalid coordinator name'),
            ({'name': '   ', 'device_id': settings.device_id}, 400, 'Invalid coordinator name'),
            ({'name': 'x' * 65, 'device_id': settings.device_id}, 400, 'Invalid coordinator name'),
            ({'name': 'Jetson', 'device_id': settings.device_id, 'port': 1}, 400, 'Invalid request'),
            ({'name': 'Jetson'}, 409, 'Device changed; refresh discovery'),
            ({'name': 'Jetson', 'device_id': 'ffeeddccbbaa9988'}, 409, 'Device changed; refresh discovery'),
        ]
        try:
            for body, code, message in expected:
                status, payload = await asyncio.to_thread(post, port, body)
                assert (status, payload['error']) == (code, message), body
        finally:
            await asyncio.to_thread(listener.shutdown)
            listener.server_close()
        assert gate.asked == [], 'a malformed request must not reach the local user'

    asyncio.run(run())


def test_an_expired_prompt_leaves_no_answer_behind():
    """A timeout is not an approval, and a late answer cannot answer a newer request."""
    async def run():
        read_fd, write_fd = os.pipe()
        approval = TerminalApproval(os.fdopen(read_fd, 'r'), io.StringIO())
        try:
            assert await approval.ask('Jetson', WORKSPACE, timeout=0.05) is False
            # The unanswered prompt still owns the one keyboard: a newer request is
            # refused outright rather than queued behind it.
            assert await approval.ask('Other', WORKSPACE, timeout=0.05) is False
            os.write(write_fd, b'y\n')  # The expired prompt's answer finally arrives.
            await asyncio.sleep(0.1)
            # It answered nothing, and it was not carried over to the next request.
            assert await approval.ask('Third', WORKSPACE, timeout=0.05) is False
        finally:
            os.close(write_fd)

    asyncio.run(run())


def test_the_retired_approval_state_is_really_gone():
    """78dc577 moved approval to the console; a resurrected queue is a regression."""
    for name in ('wait_for_approval', 'resolve', 'has_room', 'MAX_PENDING', '_pending'):
        assert not hasattr(pairing, name), name


def test_announcement_is_optional_but_the_port_is_always_recorded(monkeypatch):
    from types import SimpleNamespace

    app = SimpleNamespace(state=SimpleNamespace(pairing_port=None))
    monkeypatch.setattr(pairing, 'lan_ip', lambda: None)
    assert pairing.register_mdns(app, 8765) is None  # No LAN address to advertise.
    assert app.state.pairing_port == 8765
    monkeypatch.setattr(pairing, 'lan_ip', lambda: '192.168.1.10')
    monkeypatch.setitem(sys.modules, 'zeroconf', None)  # Simulates the extra not installed.
    assert pairing.register_mdns(app, 8765) is None
    assert not hasattr(app.state, 'zeroconf')
