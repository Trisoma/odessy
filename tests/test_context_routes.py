"""Wave 2 routes: memory CRUD, document indexing, retrieve and used context."""
import asyncio
import uuid

import pytest

from odessy.application.agent_basics import AgentBasics, BasicsCapabilities
from odessy.application.context.schema import migrate_extensions
from odessy.application.context.service import ContextService
from odessy.application.conversations.store import ConversationStore
from odessy.application.memory.routes import create_registrar as memory_registrar
from odessy.application.memory.service import MemoryService
from odessy.application.retrieval.routes import create_registrar as retrieval_registrar
from odessy.application.retrieval.service import RetrievalService

from test_retrieval_service import FakeWorkspace

pytest.importorskip('fastapi')
httpx = pytest.importorskip('httpx')


def build(tmp_path, files=None):
    from odessy.application.api import create_app
    store = ConversationStore(tmp_path / 'conversations.sqlite3')
    with store.connection() as db:
        migrate_extensions(db)
    project = store.create_project('Work')
    conversation = store.create(conversation_id=uuid.uuid4().hex, project_id=project['id'],
                                engine='legacy', profile='default', title='Chat')
    workspace = FakeWorkspace(files or {})
    retrieval = RetrievalService(store, project_workspace=workspace.project_workspace,
                                 read_file=workspace.read_file,
                                 session_conversation=lambda sid: conversation['id'])
    memory = MemoryService(store)
    context = ContextService(store, memory=memory, retrieval=retrieval)
    app = create_app(lambda: (_ for _ in ()).throw(
        AssertionError('History routes must not construct an engine')),
        token='fixture-token',
        agent_basics=AgentBasics((memory_registrar(memory), retrieval_registrar(retrieval),
                                  _context_registrar(context)),
                                 capabilities=BasicsCapabilities(memory=True, retrieval=True,
                                                                 context=True)))
    return app, store, project, conversation, workspace, memory, retrieval, context


def _context_registrar(context):
    from odessy.application.context.routes import create_registrar
    return create_registrar(context)


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url='http://test',
                             headers={'Authorization': 'Bearer fixture-token'})


def test_memory_routes_and_errors(tmp_path):
    app, store, project, conversation, workspace, memory, retrieval, context = build(tmp_path)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://test') as anonymous:
            assert (await anonymous.get('/v1/projects/' + project['id'] + '/memory')).status_code == 401
        async with client_for(app) as client:
            created = await client.post('/v1/projects/' + project['id'] + '/memory',
                                        json={'text': 'Prefers terse answers'})
            assert created.status_code == 201
            fact = created.json()
            listing = await client.get('/v1/projects/' + project['id'] + '/memory')
            assert listing.json()['items'][0]['id'] == fact['id']
            patched = await client.patch(
                '/v1/projects/' + project['id'] + '/memory/' + fact['id'],
                json={'expected_revision': 0, 'enabled': False})
            assert patched.status_code == 200 and patched.json()['enabled'] is False
            stale = await client.patch(
                '/v1/projects/' + project['id'] + '/memory/' + fact['id'],
                json={'expected_revision': 0, 'text': 'stale'})
            assert stale.status_code == 409
            unknown = await client.post('/v1/projects/' + project['id'] + '/memory',
                                        json={'text': 'x', 'extra': 1})
            assert unknown.status_code == 400
            removed = await client.request(
                'DELETE', '/v1/projects/' + project['id'] + '/memory/' + fact['id'],
                json={'expected_revision': 1})
            assert removed.status_code == 204
            missing = await client.get('/v1/projects/' + project['id'] + '/memory/' + fact['id'])
            assert missing.status_code in (404, 405)

    asyncio.run(run())


def test_document_routes_and_used_context(tmp_path):
    app, store, project, conversation, workspace, memory, retrieval, context = build(
        tmp_path, {'notes.md': 'alpha beta searchable note'})

    async def run():
        async with client_for(app) as client:
            indexed = await client.post('/v1/projects/' + project['id'] + '/documents',
                                        json={'session_id': 's1', 'service_instance_id': 'svc',
                                              'path': 'notes.md'})
            assert indexed.status_code == 201, indexed.text
            document = indexed.json()
            assert document['status'] == 'ready'
            listing = await client.get('/v1/projects/' + project['id'] + '/documents')
            assert listing.json()['documents'][0]['id'] == document['id']
            found = await client.get('/v1/projects/' + project['id'] + '/retrieve',
                                     params={'session_id': 's1', 'query': 'alpha'})
            assert found.status_code == 200, found.text
            assert found.json()['citations'][0]['path'] == 'notes.md'
            for index in range(4):
                store.append(conversation['id'], event_id=f'u{index}', kind='user',
                             payload={'text': f'alpha {index}'})
                store.append(conversation['id'], event_id=f'a{index}', kind='assistant',
                             payload={'text': f'beta {index}'})
            memory.create(project['id'], 'fact')
            context.prepare(conversation['id'], query='alpha')
            used = await client.get('/v1/conversations/' + conversation['id'] + '/context')
            assert used.status_code == 200
            body = used.json()
            assert body['conversation_id'] == conversation['id']
            assert body['chars_used'] > 0 and body['method'] == 'extractive-v1'
            stale = await client.request(
                'DELETE', '/v1/projects/' + project['id'] + '/documents/' + document['id'],
                json={'expected_revision': 99})
            assert stale.status_code == 409
            removed = await client.request(
                'DELETE', '/v1/projects/' + project['id'] + '/documents/' + document['id'],
                json={'expected_revision': 0})
            assert removed.status_code == 204

    asyncio.run(run())
