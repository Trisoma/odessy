"""Bounded prepared context: summaries, memory facts, citations and budgets."""
import asyncio
import uuid

import pytest

from odessy.application.context.schema import migrate_extensions
from odessy.application.context.service import (CITATION_LABEL, FACT_LABEL, HEADER,
                                                SUMMARY_LABEL, ContextService)
from odessy.application.conversations.store import ConversationStore, Invalid
from odessy.application.memory.service import MemoryService
from odessy.application.retrieval.service import RetrievalService
from odessy.runtime.types import ConversationTurn

from test_retrieval_service import FakeWorkspace


def setup(tmp_path, files=None):
    store = ConversationStore(tmp_path / 'conversations.sqlite3')
    with store.connection() as db:
        migrate_extensions(db)
    project = store.create_project('Work')
    conversation = store.create(conversation_id=uuid.uuid4().hex, project_id=project['id'],
                                engine='legacy', profile='default', title='Chat')
    workspace = FakeWorkspace(files)
    retrieval = RetrievalService(store, project_workspace=workspace.project_workspace,
                                 read_file=workspace.read_file,
                                 session_conversation=lambda sid: conversation['id'])
    memory = MemoryService(store)
    context = ContextService(store, memory=memory, retrieval=retrieval)
    return store, project, conversation, workspace, memory, retrieval, context


def exchange(store, conversation_id, index, user, assistant):
    store.append(conversation_id, event_id=f'u{index}', kind='user', payload={'text': user})
    store.append(conversation_id, event_id=f'a{index}', kind='assistant',
                 payload={'text': assistant})


def wrapper_chars(prepared):
    pieces = []
    if prepared.summary:
        pieces.append(SUMMARY_LABEL + prepared.summary)
    pieces.extend(FACT_LABEL + fact['text'] for fact in prepared.facts)
    pieces.extend(CITATION_LABEL + citation['text'] for citation in prepared.citations)
    prefix = (HEADER + '\n'.join(pieces)) if pieces else ''
    return len(prefix) + sum(len(turn.user) + len(turn.assistant) for turn in prepared.turns)


def test_prepare_composes_summary_facts_citations_and_recent_turns(tmp_path):
    store, project, conversation, workspace, memory, retrieval, context = setup(
        tmp_path, {'notes.md': 'alpha beta project compactor notes'})
    for index in range(6):
        exchange(store, conversation['id'], index,
                 f'question {index} alpha', f'answer {index} beta')
    fact = memory.create(project['id'], 'Prefers no emoji')
    from test_retrieval_service import ingest
    ingest(retrieval, project['id'], 'notes.md')

    prepared = context.prepare(conversation['id'], query='alpha', max_chars=24000)
    assert isinstance(prepared.turns[0], ConversationTurn)
    assert [turn.user for turn in prepared.turns] == [
        'question 3 alpha', 'question 4 alpha', 'question 5 alpha']
    assert prepared.summary and 'question 0 alpha' in prepared.summary
    assert 'question 5 alpha' not in prepared.summary  # summarized range is older only
    assert prepared.memory_ids == (fact['id'],)
    assert prepared.facts[0]['text'] == 'Prefers no emoji'
    assert prepared.citations and prepared.citations[0]['path'] == 'notes.md'
    assert prepared.revision == store.get(conversation['id'])['revision']
    assert wrapper_chars(prepared) <= 24000

    used = context.get_used(conversation['id'])
    assert used['method'] == 'extractive-v1'
    assert used['summary']['text'] == prepared.summary
    assert used['memory_ids'] == [fact['id']]
    assert [citation['path'] for citation in used['citations']] == ['notes.md']
    assert used['chars_used'] == wrapper_chars(prepared)
    assert used['budget_chars'] == 24000
    store.close()


def test_summary_is_durable_and_reused_without_duplicates(tmp_path):
    store, project, conversation, workspace, memory, retrieval, context = setup(tmp_path)
    for index in range(5):
        exchange(store, conversation['id'], index, f'user {index}', f'assistant {index}')
    first = context.prepare(conversation['id'], query='x', max_chars=24000)
    second = context.prepare(conversation['id'], query='x', max_chars=24000)
    assert first.summary == second.summary
    with store.connection() as db:
        rows = db.execute('SELECT COUNT(*) FROM context_summaries WHERE conversation_id = ?',
                          (conversation['id'],)).fetchone()[0]
        through = db.execute('SELECT through_sequence FROM context_summaries '
                             'ORDER BY through_sequence DESC LIMIT 1').fetchone()[0]
    assert rows == 1
    events = [event for event in store.raw_events(conversation['id'])
              if event['kind'] == 'assistant']
    assert through == events[1]['sequence']  # last summarized (2 older of 5)
    store.close()


def test_disabled_and_deleted_memory_disappears_from_next_request(tmp_path):
    store, project, conversation, workspace, memory, retrieval, context = setup(tmp_path)
    exchange(store, conversation['id'], 0, 'remember please', 'noted')
    fact = memory.create(project['id'], 'lighthouse preference')
    prepared = context.prepare(conversation['id'], query='lighthouse', max_chars=24000)
    assert prepared.memory_ids == (fact['id'],)
    memory.patch(project['id'], fact['id'], expected_revision=0, enabled=False)
    disabled = context.prepare(conversation['id'], query='lighthouse', max_chars=24000)
    assert disabled.memory_ids == ()
    assert 'lighthouse preference' not in disabled.summary
    memory.delete(project['id'], fact['id'], expected_revision=1)
    deleted = context.prepare(conversation['id'], query='lighthouse', max_chars=24000)
    assert deleted.memory_ids == ()
    store.close()


def test_stale_documents_are_excluded_from_prepared_citations(tmp_path):
    from test_retrieval_service import ingest
    store, project, conversation, workspace, memory, retrieval, context = setup(
        tmp_path, {'doc.md': 'citation alpha content'})
    exchange(store, conversation['id'], 0, 'question', 'answer')
    ingest(retrieval, project['id'], 'doc.md')
    assert context.prepare(conversation['id'], query='alpha').citations
    workspace.files['doc.md'] = 'changed alpha content'
    asyncio.run(retrieval.verify_project(project['id'], session_id='s1',
                                         service_instance_id='svc'))
    prepared = context.prepare(conversation['id'], query='alpha')
    assert prepared.citations == ()
    ingest(retrieval, project['id'], 'doc.md')
    assert context.prepare(conversation['id'], query='alpha').citations
    store.close()


def test_budget_is_enforced_including_labels(tmp_path):
    store, project, conversation, workspace, memory, retrieval, context = setup(
        tmp_path, {'big.md': 'alpha ' * 400})
    for index in range(4):
        exchange(store, conversation['id'], index, f'q{index} ' + 'x' * 300,
                 f'a{index} ' + 'y' * 300)
    memory.create(project['id'], 'fact ' + 'z' * 200)
    from test_retrieval_service import ingest
    ingest(retrieval, project['id'], 'big.md')
    prepared = context.prepare(conversation['id'], query='alpha', max_chars=1600)
    assert wrapper_chars(prepared) <= 1600
    assert prepared.summary and len(prepared.turns) >= 1
    used = context.get_used(conversation['id'])
    assert used['chars_used'] <= 1600
    with pytest.raises(Invalid):
        context.prepare(conversation['id'], query='alpha', max_chars=128)
    store.close()


def test_tool_and_approval_payloads_never_enter_context(tmp_path):
    store, project, conversation, workspace, memory, retrieval, context = setup(tmp_path)
    store.append(conversation['id'], event_id='u', kind='user', payload={'text': 'hello'})
    store.append(conversation['id'], event_id='call', kind='tool_call',
                 payload={'call_id': 'c1', 'name': 'SECRET-TOOL-ARG'})
    store.append(conversation['id'], event_id='result', kind='tool_result',
                 payload={'call_id': 'c1', 'name': 'run', 'status': 'ok',
                          'text': 'SECRET-TOOL-RESULT'})
    store.append(conversation['id'], event_id='approval', kind='activity',
                 payload={'text': 'SECRET-APPROVAL'})
    store.append(conversation['id'], event_id='a', kind='assistant',
                 payload={'text': 'safe answer'})
    prepared = context.prepare(conversation['id'], query='hello', max_chars=24000)
    rendered = prepared.summary + ''.join(turn.user + turn.assistant for turn in prepared.turns)
    for secret in ('SECRET-TOOL-ARG', 'SECRET-TOOL-RESULT', 'SECRET-APPROVAL'):
        assert secret not in rendered
    store.close()


def test_deletion_cascades_derived_rows_and_reopen_keeps_used_context(tmp_path):
    store, project, conversation, workspace, memory, retrieval, context = setup(tmp_path)
    for index in range(4):
        exchange(store, conversation['id'], index, f'alpha {index}', f'beta {index}')
    context.prepare(conversation['id'], query='alpha')
    assert context.get_used(conversation['id'])['chars_used'] > 0
    store.close()

    reopened = ConversationStore(tmp_path / 'conversations.sqlite3')
    with reopened.connection() as db:
        migrate_extensions(db)
    memory2 = MemoryService(reopened)
    retrieval2 = RetrievalService(reopened, project_workspace=workspace.project_workspace,
                                  read_file=workspace.read_file,
                                  session_conversation=lambda sid: conversation['id'])
    context2 = ContextService(reopened, memory=memory2, retrieval=retrieval2)
    used = context2.get_used(conversation['id'])
    assert used['revision'] and used['method'] == 'extractive-v1'
    revision = reopened.get(conversation['id'])['revision']
    reopened.delete(conversation['id'], expected_revision=revision)
    with reopened.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM context_summaries').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM context_usage').fetchone()[0] == 0
    reopened.close()
