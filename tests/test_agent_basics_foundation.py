"""Optional registrars inherit auth and never create sessions on discovery."""
import asyncio
from dataclasses import FrozenInstanceError, asdict

import pytest

from odessy.application.agent_basics import AgentBasics, BasicsCapabilities


def test_default_capabilities_are_false_and_composition_is_immutable():
    assert not any(asdict(BasicsCapabilities()).values())
    with pytest.raises(FrozenInstanceError):
        AgentBasics().registrars = ()
    with pytest.raises(TypeError):
        AgentBasics(registrars=[lambda app, deps: None])


def test_optional_mount_inherits_auth_and_looks_up_live_identity():
    pytest.importorskip('fastapi')
    httpx = pytest.importorskip('httpx')
    from fastapi import HTTPException
    from odessy.application.api import create_app

    calls = []

    def forbidden_factory():
        raise AssertionError('Discovery must not construct an engine')

    def registrar(app, dependencies):
        @app.get('/v1/foundation-fixture')
        async def discover():
            calls.append(dependencies.service_instance_id)
            return {'instance': dependencies.service_instance_id}

        @app.get('/v1/foundation-fixture/{sid}')
        async def lookup(sid: str):
            dependencies.find_session(sid)
            raise HTTPException(409, 'Fixture only')

    async def run():
        app = create_app(forbidden_factory, token='fixture-token',
                         agent_basics=AgentBasics((registrar,)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            assert (await client.get('/v1/foundation-fixture')).status_code == 401
            assert calls == []
            client.headers['Authorization'] = 'Bearer fixture-token'
            response = await client.get('/v1/foundation-fixture')
            assert response.json() == {'instance': app.state.service_instance_id}
            assert (await client.get('/v1/foundation-fixture/missing')).status_code == 404
            assert app.state.sessions == {}
        plain = create_app(forbidden_factory, token='fixture-token')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=plain),
                                     base_url='http://test',
                                     headers={'Authorization': 'Bearer fixture-token'}) as client:
            assert (await client.get('/v1/foundation-fixture')).status_code == 404

    asyncio.run(run())
