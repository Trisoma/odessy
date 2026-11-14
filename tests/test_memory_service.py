"""Explicit memory CRUD, isolation, bounds and restart persistence."""
import uuid

import pytest

from odessy.application.context.schema import migrate_extensions
from odessy.application.conversations.store import Conflict, ConversationStore, Invalid, NotFound
from odessy.application.memory.service import MAX_ENABLED, MemoryService


def setup(tmp_path):
    store = ConversationStore(tmp_path / 'conversations.sqlite3')
    with store.connection() as db:
        migrate_extensions(db)
    work = store.create_project('Work')
    other = store.create_project('Other')
    return store, work, other, MemoryService(store)


def test_crud_revisions_and_project_isolation(tmp_path):
    store, work, other, memory = setup(tmp_path)
    fact = memory.create(work['id'], 'Prefers concise answers')
    assert fact['enabled'] is True and fact['revision'] == 0 and fact['source'] == 'user'
    assert [item['id'] for item in memory.list(work['id'])] == [fact['id']]
    assert memory.list(other['id']) == []

    edited = memory.patch(work['id'], fact['id'], expected_revision=0,
                          text='Prefers very concise answers', enabled=True)
    assert edited['text'] == 'Prefers very concise answers' and edited['revision'] == 1
    with pytest.raises(Conflict):
        memory.patch(work['id'], fact['id'], expected_revision=0, text='stale')
    with pytest.raises(NotFound):
        memory.patch(other['id'], fact['id'], expected_revision=1, text='cross project')
    with pytest.raises(NotFound):
        memory.create('f' * 32, 'missing project')
    memory.delete(work['id'], fact['id'], expected_revision=1)
    assert memory.list(work['id']) == []
    with pytest.raises(NotFound):
        memory.delete(work['id'], fact['id'], expected_revision=1)
    store.close()


def test_bounds_and_bool_rejection(tmp_path):
    store, work, other, memory = setup(tmp_path)
    with pytest.raises(Invalid):
        memory.create(work['id'], 'x' * 2001)
    with pytest.raises(Invalid):
        memory.create(work['id'], '   ')
    fact = memory.create(work['id'], 'keep')
    with pytest.raises(Invalid):
        memory.patch(work['id'], fact['id'], expected_revision=0, enabled=1)
    with pytest.raises(Invalid):
        memory.patch(work['id'], fact['id'], expected_revision=True, text='bool')
    with pytest.raises(Invalid):
        memory.enabled_facts(work['id'], limit=MAX_ENABLED + 1)
    store.close()


def test_enabled_facts_are_bounded_and_never_include_disabled_or_deleted(tmp_path):
    store, work, other, memory = setup(tmp_path)
    kept = memory.create(work['id'], 'Kept fact')
    disabled = memory.create(work['id'], 'Disabled fact')
    memory.patch(work['id'], disabled['id'], expected_revision=0, enabled=False)
    deleted = memory.create(work['id'], 'Deleted fact')
    memory.delete(work['id'], deleted['id'], expected_revision=0)
    facts = memory.enabled_facts(work['id'])
    assert [fact['id'] for fact in facts] == [kept['id']]
    assert 'Disabled fact' not in [fact['text'] for fact in facts]
    assert 'Deleted fact' not in [fact['text'] for fact in facts]
    assert memory.enabled_facts(work['id'], limit=1)[0]['id'] == kept['id']
    store.close()


def test_memory_survives_store_reopen(tmp_path):
    store, work, other, memory = setup(tmp_path)
    fact = memory.create(work['id'], 'Durable preference')
    store.close()
    reopened = ConversationStore(tmp_path / 'conversations.sqlite3')
    with reopened.connection() as db:
        migrate_extensions(db)
    again = MemoryService(reopened)
    items = again.list(work['id'])
    assert [(item['id'], item['text'], item['revision']) for item in items] == [
        (fact['id'], 'Durable preference', 0)]
    assert [item['id'] for item in again.enabled_facts(work['id'])] == [fact['id']]
    reopened.close()


def test_natural_ordering_of_many_facts(tmp_path):
    store, work, other, memory = setup(tmp_path)
    ids = [memory.create(work['id'], f'fact {index}')['id'] for index in range(5)]
    listed = [item['id'] for item in memory.list(work['id'])]
    assert listed == ids
    injected = [item['id'] for item in memory.enabled_facts(work['id'])]
    assert set(injected) == set(ids) and len(injected) == 5
    store.close()
