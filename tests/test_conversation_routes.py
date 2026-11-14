"""HTTP routes for durable conversations: auth, contract errors, resume truth."""
import asyncio
import json

import pytest

from odessy.application.conversations import build_service, create_registrar

pytest.importorskip('fastapi')
httpx = pytest.importorskip('httpx')


def build(tmp_path, *, continuation=None, engine='legacy'):
    from odessy.application.agent_basics import AgentBasics, BasicsCapabilities
    from odessy.application.api import create_app

    service = build_service(path=tmp_path / 'conversations.sqlite3',
                            continuation=continuation,
                            settings=lambda: {'engine': engine, 'profile': 'default'})
    app = create_app(lambda: (_ for _ in ()).throw(
        AssertionError('History browsing must never construct an engine')),
        token='fixture-token',
        agent_basics=AgentBasics((create_registrar(service),),
                                 capabilities=BasicsCapabilities(conversations=True)))
    return app, service


def client_for(app, *, token='fixture-token'):
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url='http://test', headers=headers)


def test_conversation_routes_end_to_end(tmp_path):
    app, service = build(tmp_path)

    async def run():
        async with client_for(app, token=None) as anonymous:
            assert (await anonymous.get('/v1/projects')).status_code == 401
        async with client_for(app) as client:
            project = (await client.post('/v1/projects', json={'label': 'Work'})).json()
            created = await client.post('/v1/conversations',
                                        json={'project_id': project['id'],
                                              'title': 'First chat'})
            assert created.status_code == 201
            row = created.json()
            assert row['version'] == 1 and row['continuation'] == 'history_only'
            conversation_id = row['id']
            service.store.append(conversation_id, event_id='t1:user', kind='user',
                                 payload={'text': 'searchable compactor'})
            service.store.append(conversation_id, event_id='t1:assistant',
                                 kind='assistant', payload={'text': 'settled answer'})

            listing = (await client.get('/v1/conversations',
                                        params={'project_id': project['id']})).json()
            assert [item['id'] for item in listing['conversations']] == [conversation_id]
            patched = await client.patch(f'/v1/conversations/{conversation_id}',
                                         json={'expected_revision': 2, 'pinned': True})
            assert patched.status_code == 200 and patched.json()['pinned'] is True
            page = (await client.get(f'/v1/conversations/{conversation_id}/messages')).json()
            assert [event['kind'] for event in page['messages']] == ['user', 'assistant']
            found = (await client.get('/v1/conversations/search',
                                      params={'query': 'compactor'})).json()
            assert found['matches'][0]['conversation_id'] == conversation_id
            exported = await client.get(f'/v1/conversations/{conversation_id}/export')
            assert exported.status_code == 200
            assert exported.headers['content-disposition'].startswith('attachment;')
            assert [event['kind'] for event in exported.json()['events']] == [
                'user', 'assistant']
            deleted = await client.request(
                'DELETE', f'/v1/conversations/{conversation_id}',
                json={'expected_revision': 3})
            assert deleted.status_code == 204
            missing = await client.get(f'/v1/conversations/{conversation_id}')
            assert missing.status_code == 404
            assert missing.json()['detail']['code'] == 'not_found'
            assert (await client.get('/v1/conversations/search',
                                     params={'query': 'compactor'})).json()['matches'] == []

    asyncio.run(run())


def test_contract_errors_are_coded_and_bounded(tmp_path):
    app, service = build(tmp_path)

    async def run():
        async with client_for(app) as client:
            unknown = await client.post('/v1/projects', json={'label': 'x', 'extra': 1})
            assert unknown.status_code == 400
            assert unknown.json()['detail']['code'] == 'invalid'
            project = (await client.post('/v1/projects', json={'label': 'Work'})).json()
            missing = await client.post('/v1/conversations',
                                        json={'project_id': '0' * 32})
            assert missing.status_code == 404
            assert missing.json()['detail']['code'] == 'not_found'
            row = (await client.post('/v1/conversations',
                                     json={'project_id': project['id']})).json()
            stale = await client.patch(f'/v1/conversations/{row["id"]}',
                                       json={'expected_revision': 5, 'title': 'x'})
            assert stale.status_code == 409
            assert stale.json()['detail']['code'] == 'conflict'
            boolean = await client.patch(f'/v1/conversations/{row["id"]}',
                                         json={'expected_revision': True, 'title': 'x'})
            assert boolean.status_code == 400
            bad_page = await client.get(f'/v1/conversations/{row["id"]}/messages',
                                        params={'limit': 0})
            assert bad_page.status_code == 400
            no_query = await client.get('/v1/conversations/search')
            assert no_query.status_code == 400
            missing_conversation = await client.get('/v1/conversations/' + 'f' * 32)
            assert missing_conversation.status_code == 404

    asyncio.run(run())


def test_resume_reports_history_only_and_real_continuation(tmp_path):
    calls = []

    async def continuation(prepared):
        calls.append(prepared)
        return {'session_id': 'live-session-1'}

    harness_app, _ = build(tmp_path / 'harness', engine='harness')
    resume_app, service = build(tmp_path / 'legacy', continuation=continuation)

    async def run():
        async with client_for(harness_app) as client:
            project = (await client.post('/v1/projects', json={'label': 'W'})).json()
            row = (await client.post('/v1/conversations',
                                     json={'project_id': project['id']})).json()
            answer = await client.post(f'/v1/conversations/{row["id"]}/resume',
                                       json={'expected_revision': 0})
            assert answer.status_code == 501
            assert answer.json()['detail']['code'] == 'unsupported'
        async with client_for(resume_app) as client:
            project = (await client.post('/v1/projects', json={'label': 'W'})).json()
            row = (await client.post('/v1/conversations',
                                     json={'project_id': project['id']})).json()
            service.store.append(row['id'], event_id='u', kind='user',
                                 payload={'text': 'continue me'})
            service.store.append(row['id'], event_id='a', kind='assistant',
                                 payload={'text': 'of course'})
            resumed = await client.post(f'/v1/conversations/{row["id"]}/resume',
                                        json={'expected_revision': 2})
            assert resumed.status_code == 200
            body = resumed.json()
            assert body['session_id'] == 'live-session-1'
            assert body['continuation'] == 'continuable'
            assert body['service_instance_id']
            stale = await client.post(f'/v1/conversations/{row["id"]}/resume',
                                      json={'expected_revision': 1})
            assert stale.status_code == 409
            assert calls[0]['turns'] == [{'user': 'continue me', 'assistant': 'of course'}]

    asyncio.run(run())


def test_list_cursor_is_bound_to_its_query_scope(tmp_path):
    app, _ = build(tmp_path)

    async def run():
        async with client_for(app) as client:
            first = (await client.post('/v1/projects', json={'label': 'One'})).json()
            second = (await client.post('/v1/projects', json={'label': 'Two'})).json()
            for title in ('a', 'b'):
                await client.post('/v1/conversations',
                                  json={'project_id': first['id'], 'title': title})
            listing = (await client.get('/v1/conversations',
                                        params={'project_id': first['id'],
                                                'limit': 1})).json()
            cursor = listing['next_cursor']
            assert cursor
            mismatch = await client.get('/v1/conversations',
                                        params={'project_id': second['id'],
                                                'cursor': cursor})
            assert mismatch.status_code == 409
            same = (await client.get('/v1/conversations',
                                     params={'project_id': first['id'],
                                             'cursor': cursor})).json()
            assert len(same['conversations']) == 1

    asyncio.run(run())
