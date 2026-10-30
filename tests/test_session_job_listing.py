"""Authenticated session job listing, using in-memory jobs only."""
import asyncio

import httpx
import pytest

from odessy.application.api import create_app
from odessy.application.session import SessionController
from odessy.collaboration.jobs import JobManager


def job(job_id, participant='coder'):
    return {'job_id': job_id, 'participant': participant, 'mode': 'advice',
            'state': 'completed', 'remote_state_unknown': False,
            'task': 'private task', 'log': 'private log', 'result': 'private result'}


def test_manager_pagination_is_bounded_and_copies_summaries():
    manager = JobManager({})
    ids = [f'{i:032x}' for i in range(3)]
    manager._jobs = {job_id: job(job_id) for job_id in ids}
    first = manager.list_summaries(limit=2)
    assert [item['job_id'] for item in first['jobs']] == ids[:2]
    assert first['next_cursor'] == ids[1]
    first['jobs'][0]['state'] = 'altered'
    assert manager.get(ids[0])['state'] == 'completed'
    second = manager.list_summaries(after=first['next_cursor'], limit=2)
    assert [item['job_id'] for item in second['jobs']] == ids[2:]
    assert second['next_cursor'] is None
    assert 'task' not in second['jobs'][0] and 'log' not in second['jobs'][0]
    for bad in (0, 51, True, -1):
        with pytest.raises(ValueError):
            manager.list_summaries(limit=bad)
    for bad in ('x', 'a' * 32):
        with pytest.raises(ValueError):
            manager.list_summaries(after=bad)


def test_authenticated_session_list_is_isolated_and_rejects_bad_queries():
    async def run():
        managers = []

        def factory():
            manager = JobManager({})
            managers.append(manager)
            return SessionController(model_factory=lambda name: name,
                                     agent_factory=lambda *a, **k: None,
                                     profiles={'local': {}}, jobs=manager)

        app = create_app(factory, token='fixture-token')
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            headers = {'Authorization': 'Bearer fixture-token'}
            assert (await client.get('/v1/sessions/unknown/jobs')).status_code == 401
            first = (await client.post('/v1/sessions', headers=headers)).json()['id']
            second = (await client.post('/v1/sessions', headers=headers)).json()['id']
            assert (await client.get(f'/v1/sessions/{first}/jobs', headers=headers)).json() == {
                'jobs': [], 'next_cursor': None}
            first_ids = ['1' * 32, '2' * 32, '3' * 32]
            second_id = '4' * 32
            managers[0]._jobs = {i: job(i) for i in first_ids}
            managers[1]._jobs = {second_id: job(second_id, 'other')}
            route = f'/v1/sessions/{first}/jobs'
            result = await client.get(route, params={'limit': 2}, headers=headers)
            assert result.status_code == 200
            data = result.json()
            assert [j['job_id'] for j in data['jobs']] == first_ids[:2]
            assert 'private' not in result.text
            assert (await client.get(route, params={'after': data['next_cursor']}, headers=headers)).json()['jobs'][0]['job_id'] == first_ids[2]
            other = (await client.get(f'/v1/sessions/{second}/jobs', headers=headers)).json()
            assert [j['job_id'] for j in other['jobs']] == [second_id]
            assert (await client.get(route, params={'after': second_id}, headers=headers)).status_code == 400
            for query in ('limit=0', 'limit=51', 'limit=1&limit=2', 'extra=1', 'after=invalid'):
                assert (await client.get(route + '?' + query, headers=headers)).status_code == 400
            assert (await client.get('/v1/sessions/missing/jobs', headers=headers)).status_code == 404
            assert (await client.delete(f'/v1/sessions/{first}', headers=headers)).status_code == 204
            assert (await client.get(route, headers=headers)).status_code == 404
            assert (await client.delete(f'/v1/sessions/{second}', headers=headers)).status_code == 204
    asyncio.run(run())


def test_unconfigured_session_returns_unavailable():
    async def run():
        app = create_app(lambda: SessionController(model_factory=lambda name: name,
            agent_factory=lambda *a, **k: None, profiles={'local': {}}), token='fixture-token')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test',
                                     headers={'Authorization': 'Bearer fixture-token'}) as client:
            sid = (await client.post('/v1/sessions')).json()['id']
            assert (await client.get(f'/v1/sessions/{sid}/jobs')).status_code == 503
            assert (await client.delete(f'/v1/sessions/{sid}')).status_code == 204
    asyncio.run(run())


def test_session_snapshot_exposes_only_its_own_model_choices():
    async def run():
        choices = [{'current': {}, 'old': {}}, {'current': {}, 'new': {}}]
        def factory():
            return SessionController(model_factory=lambda name: name,
                agent_factory=lambda *a, **k: None, profiles=choices.pop(0))
        app = create_app(factory, token='fixture-token', profiles={'global': {}})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                base_url='http://test', headers={'Authorization': 'Bearer fixture-token'}) as client:
            first = (await client.post('/v1/sessions')).json()
            second = (await client.post('/v1/sessions')).json()
            assert first['available_profiles'] == ['current', 'old']
            assert second['available_profiles'] == ['current', 'new']
            route = f"/v1/sessions/{first['id']}"
            assert (await client.get(route)).json()['available_profiles'] == ['current', 'old']
            assert (await client.put(route + '/model', json={'profile': 'new'})).status_code == 400
            assert (await client.put(route + '/model', json={'profile': 'old'})).status_code == 200
            await client.delete(route)
            await client.delete(f"/v1/sessions/{second['id']}")
    asyncio.run(run())
