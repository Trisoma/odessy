"""HTTP wiring for access modes: auth, revisions, honest unsupported state."""
from __future__ import annotations

import asyncio

import pytest

from odessy.application.access_modes import AccessModeService
from odessy.application.agent_basics import AgentBasics, BasicsDependencies, ReviewInput


class Controller:
    def __init__(self, engine: str = 'legacy') -> None:
        self.engine = engine


def harness(*, engine='legacy', with_scope=True):
    pytest.importorskip('fastapi')
    httpx = pytest.importorskip('httpx')
    from odessy.application.access_mode_routes import register
    from odessy.application.api import create_app

    holder: dict = {}

    def registrar(app, dependencies):
        resolve = ((lambda sid, controller: {'workspace_id': 'ws-1',
                                             'target_id': 'local-laptop'})
                   if with_scope else None)
        service = AccessModeService(dependencies, resolve_scope=resolve)
        holder['service'] = service
        register(app, dependencies, service=service)

    app = create_app(lambda: (_ for _ in ()).throw(AssertionError('no engine')),
                     token='fixture-token',
                     agent_basics=AgentBasics((registrar,)))
    app.state.sessions['s1'] = Controller(engine)
    return app, holder, httpx


def call(app, httpx, method, path, *, token='fixture-token', body=None):
    async def run():
        headers = {} if token is None else {'Authorization': f'Bearer {token}'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test', headers=headers) as client:
            return await client.request(method, path, json=body)

    return asyncio.run(run())


def mode_body(app, mode='auto', revision=0, service=None):
    return {'mode': mode, 'expected_revision': revision,
            'service_instance_id': app.state.service_instance_id
            if service is None else service}


def test_routes_require_the_existing_bearer_authentication():
    app, _, httpx = harness()
    assert call(app, httpx, 'GET', '/v1/sessions/s1/access-mode', token=None).status_code == 401
    assert call(app, httpx, 'PUT', '/v1/sessions/s1/access-mode', token=None,
                body=mode_body(app)).status_code == 401
    assert call(app, httpx, 'GET', '/v1/sessions/s1/access-mode',
                token='wrong').status_code == 401
    assert app.state.sessions['s1'].engine == 'legacy'


def test_get_reports_ask_zero_and_never_mutates():
    app, _, httpx = harness()
    first = call(app, httpx, 'GET', '/v1/sessions/s1/access-mode')
    assert first.status_code == 200
    payload = first.json()
    assert payload == {
        'version': 1,
        'session_id': 's1',
        'service_instance_id': app.state.service_instance_id,
        'mode': 'ask',
        'revision': 0,
        'supported_modes': ['ask', 'auto', 'full'],
        'reviewer': 'policy-v1',
        'scope': {'workspace_id': 'ws-1', 'target_id': 'local-laptop'},
        'effective': True,
    }
    for _ in range(5):
        assert call(app, httpx, 'GET', '/v1/sessions/s1/access-mode').json() == payload


def test_put_switches_mode_once_and_reports_the_new_revision():
    app, holder, httpx = harness()
    changed = call(app, httpx, 'PUT', '/v1/sessions/s1/access-mode',
                   body=mode_body(app, 'auto'))
    assert changed.status_code == 200
    assert changed.json()['mode'] == 'auto' and changed.json()['revision'] == 1
    assert call(app, httpx, 'GET', '/v1/sessions/s1/access-mode').json()['mode'] == 'auto'

    stale = call(app, httpx, 'PUT', '/v1/sessions/s1/access-mode',
                 body=mode_body(app, 'full', revision=0))
    assert stale.status_code == 409
    assert stale.json()['detail']['code'] == 'conflict'

    # The route and the reviewer share one service instance; a decision prepared
    # before the switch cannot pass the dispatch barrier afterwards.
    service = holder['service']
    request = ReviewInput(
        session_id='s1', service_instance_id=app.state.service_instance_id,
        mode_revision=1, operation='filesystem.write', fingerprint='fp',
        decision='confirm', rule='workspace_write', risk='medium',
        scopes=('authorization',), scope_authorized=True, current_binding=True,
        content_bound_review=True)
    grant = service.review(request)
    assert grant.outcome == 'approve'
    current = call(app, httpx, 'GET', '/v1/sessions/s1/access-mode').json()['revision']
    assert current == 1
    assert service.validate(grant, request) is True


def test_put_rejects_bad_bodies_without_changing_state():
    app, _, httpx = harness()
    cases = [
        (mode_body(app, 'yolo'), 400),
        (mode_body(app, 'auto', service='other-instance'), 409),
        ({'mode': 'auto', 'expected_revision': True,
          'service_instance_id': app.state.service_instance_id}, 400),
        ({'mode': 'auto', 'expected_revision': '0',
          'service_instance_id': app.state.service_instance_id}, 400),
        ({'mode': 'auto', 'expected_revision': 0}, 400),
        ({'mode': 'auto', 'expected_revision': 0,
          'service_instance_id': app.state.service_instance_id, 'extra': 1}, 400),
        ({}, 400),
    ]
    for body, status in cases:
        response = call(app, httpx, 'PUT', '/v1/sessions/s1/access-mode', body=body)
        assert response.status_code == status, (body, response.text)
        assert 'code' in response.json()['detail']
    assert call(app, httpx, 'GET', '/v1/sessions/s1/access-mode').json()['revision'] == 0


def test_put_rejects_non_object_json():
    app, _, httpx = harness()

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test',
                                     headers={'Authorization': 'Bearer fixture-token'}) as client:
            return await client.put('/v1/sessions/s1/access-mode',
                                    content=b'[1,2,3]',
                                    headers={'content-type': 'application/json'})

    assert asyncio.run(run()).status_code == 400


def test_unknown_session_is_404():
    app, _, httpx = harness()
    assert call(app, httpx, 'GET', '/v1/sessions/ghost/access-mode').status_code == 404
    assert call(app, httpx, 'PUT', '/v1/sessions/ghost/access-mode',
                body=mode_body(app)).status_code == 404


def test_harness_advertises_no_modes_and_refuses_mutation():
    app, _, httpx = harness(engine='harness')
    payload = call(app, httpx, 'GET', '/v1/sessions/s1/access-mode').json()
    assert payload['supported_modes'] == []
    assert payload['effective'] is False
    response = call(app, httpx, 'PUT', '/v1/sessions/s1/access-mode',
                    body=mode_body(app, 'auto'))
    assert response.status_code == 501
    assert response.json()['detail']['code'] == 'unsupported'
    assert call(app, httpx, 'GET', '/v1/sessions/s1/access-mode').json()['mode'] == 'ask'


def test_missing_scope_resolver_is_reported_not_guessed():
    app, _, httpx = harness(with_scope=False)
    payload = call(app, httpx, 'GET', '/v1/sessions/s1/access-mode').json()
    assert payload['scope'] is None
    assert payload['effective'] is False
    assert call(app, httpx, 'PUT', '/v1/sessions/s1/access-mode',
                body=mode_body(app)).status_code == 409
