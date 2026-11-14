"""Deterministic lexical retrieval, citations, drift and path refusals."""
import asyncio
import hashlib
import uuid

import pytest

from odessy.application.context.schema import migrate_extensions
from odessy.application.conversations.store import (Conflict, ConversationStore, Invalid,
                                                    NotFound, PayloadTooLarge)
from odessy.application.retrieval.service import RetrievalService


class FakeWorkspace:
    def __init__(self, files=None):
        self.files = dict(files or {})
        self.denied = set()
        self.calls = []

    def project_workspace(self, project_id, session_id, service_instance_id=None):
        assert session_id == 's1', session_id
        assert service_instance_id in (None, 'svc')
        return 'binding'

    async def read_file(self, conversation_id, path):
        self.calls.append((conversation_id, path))
        if path in self.denied:
            raise PermissionError('denied')
        if path not in self.files:
            raise FileNotFoundError(path)
        text = self.files[path]
        return {'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                'path': path}


def setup(tmp_path, files=None):
    store = ConversationStore(tmp_path / 'conversations.sqlite3')
    with store.connection() as db:
        migrate_extensions(db)
    project = store.create_project('Work')
    conversation = store.create(conversation_id=uuid.uuid4().hex, project_id=project['id'],
                                engine='legacy', profile='default', title='Chat')
    workspace = FakeWorkspace(files)
    service = RetrievalService(store, project_workspace=workspace.project_workspace,
                               read_file=workspace.read_file,
                               session_conversation=lambda sid: conversation['id'])
    return store, project, conversation, workspace, service


def ingest(service, project_id, path):
    return asyncio.run(service.ingest(project_id, session_id='s1',
                                      service_instance_id='svc', path=path))


def test_ingest_chunks_and_retrieve_citations(tmp_path):
    text = '\n'.join(f'line {index} alpha beta' for index in range(1, 601))
    store, project, conversation, workspace, service = setup(tmp_path, {'notes.md': text})
    document = ingest(service, project['id'], 'notes.md')
    assert document['status'] == 'ready' and document['chunks'] > 1
    assert document['source_hash'] == hashlib.sha256(text.encode()).hexdigest()
    result = service.retrieve(project['id'], 'alpha', limit=3)
    assert result['citations'] and result['stale_document_ids'] == []
    citation = result['citations'][0]
    assert citation['path'] == 'notes.md' and citation['text']
    assert citation['start_line'] >= 1 and citation['end_line'] >= citation['start_line']
    assert citation['source_hash'] == document['source_hash']
    assert str(citation['start_line']) in citation['text'].split('\n')[0]
    hostile = service.retrieve(project['id'], '" OR * NEAR(')
    assert isinstance(hostile['citations'], list)
    with pytest.raises(Invalid):
        service.retrieve(project['id'], '   ')
    store.close()


def test_ranking_is_deterministic_and_lexical_fallback_works(tmp_path):
    files = {'a.md': 'compactor compactor compactor\ncontext',
             'b.md': 'compactor appears once here'}
    store, project, conversation, workspace, service = setup(tmp_path, files)
    ingest(service, project['id'], 'a.md')
    ingest(service, project['id'], 'b.md')
    first = service.retrieve(project['id'], 'compactor', limit=5)['citations']
    second = service.retrieve(project['id'], 'compactor', limit=5)['citations']
    assert [(c['path'], c['start_line'], c['score']) for c in first] == [
        (c['path'], c['start_line'], c['score']) for c in second]
    assert first and first[0]['path'] == 'a.md'

    service._fts = False  # packaged SQLite without FTS5
    fallback = service.retrieve(project['id'], 'compactor', limit=5)['citations']
    assert fallback and fallback[0]['path'] == 'a.md'
    assert [c['path'] for c in fallback] == [c['path'] for c in first]
    store.close()


def test_source_drift_marks_stale_and_refresh_recovers(tmp_path):
    store, project, conversation, workspace, service = setup(
        tmp_path, {'guide.md': 'original guidance about alpha'})
    ingest(service, project['id'], 'guide.md')
    assert service.retrieve(project['id'], 'alpha')['citations']
    workspace.files['guide.md'] = 'rewritten guidance about beta'
    verified = asyncio.run(service.verify_project(project['id'], session_id='s1',
                                                  service_instance_id='svc'))
    assert verified['documents'][0]['status'] == 'stale'
    result = service.retrieve(project['id'], 'alpha')
    assert result['citations'] == [] and result['stale_document_ids'] == [verified['documents'][0]['id']]
    refreshed = ingest(service, project['id'], 'guide.md')
    assert refreshed['status'] == 'ready' and refreshed['revision'] == 1
    assert service.retrieve(project['id'], 'beta')['citations']


def test_sensitive_escape_binary_and_extension_refusals(tmp_path):
    store, project, conversation, workspace, service = setup(tmp_path)
    refused = ['.env', 'config/.env.local', '../outside.md', '/absolute.md', 'C:/x.md',
               '.git/config', 'secrets/id_rsa', 'notes.exe', 'package-lock.json']
    for path in refused:
        with pytest.raises(Invalid):
            ingest(service, project['id'], path)
    workspace.files['binary.md'] = 'text\x00with nul'
    with pytest.raises(Invalid):
        ingest(service, project['id'], 'binary.md')
    workspace.files['huge.md'] = 'x' * (256 * 1024 + 1)
    with pytest.raises(PayloadTooLarge):
        ingest(service, project['id'], 'huge.md')
    assert service.documents(project['id']) == []
    store.close()


def test_authorization_and_project_isolation(tmp_path):
    store, project, conversation, workspace, service = setup(
        tmp_path, {'one.md': 'shared word'})
    other = store.create_project('Other')

    def denied(project_id, session_id, service_instance_id=None):
        raise PermissionError('Project is not bound to this live conversation')

    service.project_workspace = denied
    with pytest.raises(PermissionError):
        ingest(service, project['id'], 'one.md')
    service.project_workspace = workspace.project_workspace
    ingest(service, project['id'], 'one.md')
    assert service.documents(other['id']) == []
    assert service.retrieve(other['id'], 'shared')['citations'] == []
    store.close()


def test_remove_requires_revision_and_persists_across_reopen(tmp_path):
    store, project, conversation, workspace, service = setup(
        tmp_path, {'doc.md': 'persistent alpha content'})
    document = ingest(service, project['id'], 'doc.md')
    with pytest.raises(Conflict):
        service.delete(project['id'], document['id'], expected_revision=9)
    with pytest.raises(NotFound):
        service.delete(project['id'], 'f' * 32, expected_revision=0)
    store.close()

    reopened = ConversationStore(tmp_path / 'conversations.sqlite3')
    with reopened.connection() as db:
        migrate_extensions(db)
    second = RetrievalService(reopened, project_workspace=workspace.project_workspace,
                              read_file=workspace.read_file,
                              session_conversation=lambda sid: conversation['id'])
    assert second.documents(project['id'])[0]['id'] == document['id']
    assert second.retrieve(project['id'], 'persistent')['citations']
    second.delete(project['id'], document['id'], expected_revision=document['revision'])
    assert second.documents(project['id']) == []
    assert second.retrieve(project['id'], 'persistent')['citations'] == []
    reopened.close()


def test_refresh_same_path_is_idempotent(tmp_path):
    store, project, conversation, workspace, service = setup(
        tmp_path, {'same.md': 'same text'})
    first = ingest(service, project['id'], 'same.md')
    again = ingest(service, project['id'], 'same.md')
    assert again['id'] == first['id'] and again['revision'] == first['revision'] + 1
    assert again['chunks'] == first['chunks']
    assert len(service.documents(project['id'])) == 1
    store.close()
