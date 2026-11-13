"""Session discovery never constructs a runtime or discloses its history."""
import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip('fastapi')
httpx = pytest.importorskip('httpx')
from odessy.application.api import create_app


@pytest.mark.parametrize('engine', ['legacy', 'harness'])
def test_directory_auth_bounds_identity_and_no_runtime_effects(engine):
    async def run():
        def forbidden_factory():
            raise AssertionError('Listing must not create an engine')

        app = create_app(forbidden_factory, token='fixture-secret')
        controller = SimpleNamespace(id='existing', engine=engine,
                                     selected_profile='local', running=False,
                                     history='private fixture history', api_key='private')
        app.state.sessions['existing'] = controller
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as client:
            assert (await client.get('/v1/sessions')).status_code == 401
            headers = {'Authorization': 'Bearer fixture-secret'}
            result = await client.get('/v1/sessions', headers=headers)
            assert result.status_code == 200
            assert result.json() == {
                'service_instance_id': app.state.service_instance_id,
                'sessions': [{'id': 'existing', 'engine': engine,
                              'selected_profile': 'local', 'running': False}],
                'next_offset': None,
            }
            assert 'private' not in result.text
            for query in ('limit=0', 'limit=101', 'offset=-1', 'offset=100001'):
                assert (await client.get('/v1/sessions?' + query, headers=headers)).status_code == 400
            assert (await client.get('/v1/sessions?offset=1', headers=headers)).json()['sessions'] == []
            controller.running = True
            assert (await client.get('/v1/sessions', headers=headers)).json()['sessions'][0]['running']
            app.state.sessions.clear()
            assert (await client.get('/v1/sessions', headers=headers)).json()['sessions'] == []

    asyncio.run(run())


def test_directory_pagination_and_service_restart_identity():
    async def run():
        app = create_app(lambda: None, token='fixture-secret')
        other = create_app(lambda: None, token='fixture-secret')
        assert app.state.service_instance_id != other.state.service_instance_id
        for i in range(3):
            app.state.sessions[str(i)] = SimpleNamespace(id=str(i), engine='legacy',
                                                        selected_profile='local', running=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer fixture-secret'}) as client:
            first = (await client.get('/v1/sessions?limit=2')).json()
            assert [s['id'] for s in first['sessions']] == ['0', '1']
            assert first['next_offset'] == 2
            last = (await client.get('/v1/sessions?limit=2&offset=2')).json()
            assert [s['id'] for s in last['sessions']] == ['2']
            assert last['next_offset'] is None

    asyncio.run(run())
