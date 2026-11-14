"""Durable conversation store: restart, paging, search, deletion, corruption."""
import sqlite3
import uuid

import pytest

from odessy.application.conversations.store import (Conflict, ConversationStore,
                                                    CorruptDatabase, Invalid, NotFound,
                                                    PayloadTooLarge, SchemaTooNew,
                                                    StorageError)


def make_store(tmp_path, name='conversations.sqlite3', **kwargs):
    return ConversationStore(tmp_path / name, **kwargs)


def seed(store, *, title='First', project='Work', engine='legacy', profile='default'):
    project_row = store.create_project(project)
    row = store.create(conversation_id=uuid.uuid4().hex, project_id=project_row['id'],
                       engine=engine, profile=profile, title=title)
    return project_row, row


def test_history_survives_close_and_reopen(tmp_path):
    store = make_store(tmp_path)
    project, conversation = seed(store)
    first = store.append(conversation['id'], event_id='turn-1:user', kind='user',
                         payload={'text': 'hello'}, created_at='2026-11-14T01:02:03Z')
    second = store.append(conversation['id'], event_id='turn-1:assistant',
                          kind='assistant', payload={'text': 'hi there'})
    assert (first['sequence'], second['sequence']) == (1, 2)
    store.close()

    reopened = make_store(tmp_path)
    row = reopened.get(conversation['id'])
    assert row['revision'] == 2 and row['engine'] == 'legacy'
    page = reopened.messages(conversation['id'])
    assert [event['kind'] for event in page['messages']] == ['user', 'assistant']
    assert page['messages'][0]['created_at'] == '2026-11-14T01:02:03Z'
    assert reopened.projects()[0]['label'] == project['label']
    reopened.close()


def test_append_is_idempotent_and_rejects_changed_payload(tmp_path):
    store = make_store(tmp_path)
    _, conversation = seed(store)
    first = store.append(conversation['id'], event_id='same', kind='user',
                         payload={'text': 'once'})
    again = store.append(conversation['id'], event_id='same', kind='user',
                         payload={'text': 'once'})
    assert again == first
    assert store.get(conversation['id'])['revision'] == 1
    with pytest.raises(Conflict):
        store.append(conversation['id'], event_id='same', kind='user',
                     payload={'text': 'different'})


def test_messages_pages_are_exclusive_and_hide_stream_deltas(tmp_path):
    store = make_store(tmp_path)
    _, conversation = seed(store)
    for index in range(5):
        store.append(conversation['id'], event_id=f'u{index}', kind='user',
                     payload={'text': f'question {index}'})
        store.append(conversation['id'], event_id=f'a{index}', kind='assistant',
                     payload={'text': f'answer {index}'})
        store.append(conversation['id'], event_id=f'd{index}', kind='assistant_delta',
                     payload={'text': f'answer {index}'})
    seen, cursor, pages = [], None, 0
    while True:
        page = store.messages(conversation['id'], before=cursor, limit=4)
        assert [event['kind'] for event in page['messages']] == [
            'user', 'assistant'] * (len(page['messages']) // 2)
        sequences = [event['sequence'] for event in page['messages']]
        assert sequences == sorted(sequences)
        seen = sequences + seen
        pages += 1
        cursor = page['next_before']
        if cursor is None:
            break
    assert pages == 3 and len(seen) == 10 and len(set(seen)) == 10
    export = store.export(conversation['id'])
    assert all(event['kind'] != 'assistant_delta' for event in export['events'])


def test_list_orders_pinned_first_and_pages_with_scope(tmp_path):
    store = make_store(tmp_path)
    project, first = seed(store, title='One')
    second = store.create(conversation_id='b' * 32, project_id=project['id'],
                          engine='legacy', profile='default', title='Two')
    third = store.create(conversation_id='c' * 32, project_id=project['id'],
                         engine='harness', profile='fast', title='Three')
    store.patch(first['id'], expected_revision=0, pinned=True)
    first_page = store.list(project_id=project['id'], limit=1)
    assert first_page['next_cursor'] is not None
    with pytest.raises(Conflict):
        store.list(project_id='f' * 32, cursor=first_page['next_cursor'])
    pages, cursor = [], first_page['next_cursor']
    pages.extend(row['title'] for row in first_page['conversations'])
    while cursor is not None:
        page = store.list(project_id=project['id'], limit=1, cursor=cursor)
        pages.extend(row['title'] for row in page['conversations'])
        cursor = page['next_cursor']
    assert pages == ['One', 'Three', 'Two']
    archived = store.list(project_id=project['id'], archived=False)
    assert len(archived['conversations']) == 3
    store.patch(second['id'], expected_revision=0, archived=True)
    assert [row['title'] for row in store.list(project_id=project['id'])['conversations']] == [
        'One', 'Three']
    assert [row['title'] for row in store.list(archived=True)['conversations']] == ['Two']
    assert third['engine'] == 'harness'


def test_bounds_and_bool_as_int_are_rejected(tmp_path):
    store = make_store(tmp_path)
    project, conversation = seed(store)
    with pytest.raises(Invalid):
        store.create(conversation_id='f' * 32, project_id=project['id'],
                     engine='legacy', profile='default', title='x' * 121)
    with pytest.raises(Invalid):
        store.create(conversation_id='not-a-durable-id', project_id=project['id'],
                     engine='legacy', profile='default', title='nope')
    with pytest.raises(Invalid):
        store.create(conversation_id='f' * 32, project_id=project['id'],
                     engine='other', profile='default', title='nope')
    with pytest.raises(Invalid):
        store.patch(conversation['id'], expected_revision=True, title='nope')
    with pytest.raises(Invalid):
        store.append(conversation['id'], event_id='bad', kind='user',
                     payload={'text': 'x' * (256 * 1024)})
    with pytest.raises(PayloadTooLarge):
        store.append(conversation['id'], event_id='big', kind='user',
                     payload={'text': 'x' * (256 * 1024)})


def test_search_is_literal_bounded_and_free_of_deltas(tmp_path):
    store = make_store(tmp_path)
    project, conversation = seed(store)
    store.append(conversation['id'], event_id='u', kind='user',
                 payload={'text': 'please find the alpha beta note'})
    store.append(conversation['id'], event_id='d', kind='assistant_delta',
                 payload={'text': 'alpha appears once'})
    store.append(conversation['id'], event_id='a', kind='assistant',
                 payload={'text': 'alpha appears once'})
    matches = store.search(query='alpha')['matches']
    assert [(m['sequence'], m['snippet']) for m in matches] == [
        (1, 'please find the alpha beta note'), (3, 'alpha appears once')]
    hostile = store.search(query='" OR 1=1 -- *')['matches']
    assert hostile == []
    assert store.search(query='alpha', limit=1)['next_cursor'] is not None
    with pytest.raises(Invalid):
        store.search(query='   ')
    with pytest.raises(Invalid):
        store.search(query='x' * 513)


def test_export_is_bounded(tmp_path):
    store = make_store(tmp_path)
    _, conversation = seed(store)
    for index in range(9):
        store.append(conversation['id'], event_id=f'e{index}', kind='user',
                     payload={'text': 'x' * (250 * 1024)})
    with pytest.raises(PayloadTooLarge):
        store.export(conversation['id'])


def test_delete_requires_revision_and_invalidates_owned_data(tmp_path):
    store = make_store(tmp_path)
    _, conversation = seed(store)
    store.append(conversation['id'], event_id='u', kind='user',
                 payload={'text': 'delete me'})
    assert store.search(query='delete')['matches']
    cleaned = []
    store.register_before_delete(cleaned.append)
    with pytest.raises(Conflict):
        store.delete(conversation['id'], expected_revision=0)
    with pytest.raises(NotFound):
        store.delete('e' * 32, expected_revision=0)
    assert cleaned == []
    store.delete(conversation['id'], expected_revision=1)
    assert cleaned == [conversation['id']]
    with pytest.raises(NotFound):
        store.get(conversation['id'])
    assert store.search(query='delete')['matches'] == []
    assert store.raw_events(conversation['id']) == ()


def test_deletion_callbacks_run_first_and_can_refuse(tmp_path):
    store = make_store(tmp_path)
    _, conversation = seed(store)
    store.append(conversation['id'], event_id='u', kind='user', payload={'text': 'kept'})
    seen = []

    def failing(conversation_id):
        seen.append(conversation_id)
        raise RuntimeError('index locked')

    store.register_before_delete(failing)
    with pytest.raises(StorageError):
        store.delete(conversation['id'], expected_revision=1)
    assert store.get(conversation['id'])['id'] == conversation['id']


def test_corrupt_database_is_rejected_without_reset(tmp_path):
    path = tmp_path / 'conversations.sqlite3'
    path.write_bytes(b'not a sqlite database at all')
    with pytest.raises(CorruptDatabase):
        ConversationStore(path)
    assert path.read_bytes() == b'not a sqlite database at all'


def test_future_schema_is_rejected_without_reset(tmp_path):
    store = make_store(tmp_path)
    store.close()
    with sqlite3.connect(tmp_path / 'conversations.sqlite3') as db:
        db.execute('PRAGMA user_version = 99')
    with pytest.raises(SchemaTooNew):
        ConversationStore(tmp_path / 'conversations.sqlite3')
    with sqlite3.connect(tmp_path / 'conversations.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 99


def test_disk_failure_is_not_acknowledged(tmp_path):
    def fault(operation):
        if operation == 'append':
            raise OSError('database or disk is full')

    store = ConversationStore(tmp_path / 'conversations.sqlite3', fault=fault)
    project = store.create_project('Work')
    row = store.create(conversation_id='d' * 32, project_id=project['id'],
                       engine='legacy', profile='default', title='Pending')
    with pytest.raises(StorageError):
        store.append(row['id'], event_id='u', kind='user', payload={'text': 'not saved'})
    assert store.raw_events(row['id']) == ()
    store.close()
    assert make_store(tmp_path).messages(row['id'])['messages'] == []


def test_conversations_are_isolated_across_projects(tmp_path):
    store = make_store(tmp_path)
    work, first = seed(store, title='Work chat', project='Work')
    play, second = seed(store, title='Play chat', project='Play')
    store.append(first['id'], event_id='u', kind='user', payload={'text': 'work secret'})
    store.append(second['id'], event_id='u', kind='user', payload={'text': 'play secret'})
    assert [event['payload']['text'] for event in
            store.messages(first['id'])['messages']] == ['work secret']
    assert store.search(query='secret', project_id=work['id'])['matches'][0][
        'conversation_id'] == first['id']
    assert store.search(query='secret', project_id=play['id'])['matches'][0][
        'conversation_id'] == second['id']
    assert [row['title'] for row in store.list(project_id=play['id'])['conversations']] == [
        'Play chat']


def test_extension_connection_keeps_core_schema_version(tmp_path):
    store = make_store(tmp_path)
    with store.connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS context_summaries (id TEXT PRIMARY KEY)')
        db.execute("INSERT OR REPLACE INTO schema_extensions (name,version) VALUES ('context',1)")
        db.commit()
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
    store.close()
    reopened = make_store(tmp_path)
    with reopened.connection() as db:
        assert db.execute("SELECT version FROM schema_extensions WHERE name='context'"
                          ).fetchone()[0] == 1


def test_prepare_resume_returns_turns_and_visible_unknown_tool(tmp_path):
    store = make_store(tmp_path)
    _, conversation = seed(store)
    store.append(conversation['id'], event_id='u1', kind='user', payload={'text': 'ask'})
    store.append(conversation['id'], event_id='a1', kind='assistant',
                 payload={'text': 'answer'})
    store.append(conversation['id'], event_id='call', kind='tool_call',
                 payload={'call_id': 'c1', 'name': 'read_file'})
    prepared = store.prepare_resume(conversation['id'])
    assert prepared['turns'] == [{'user': 'ask', 'assistant': 'answer'}]
    assert [item['kind'] for item in prepared['unfinished']] == ['tool_call']
    store.append(conversation['id'], event_id='result', kind='tool_result',
                 payload={'call_id': 'c1', 'name': 'read_file', 'status': 'ok'})
    assert store.prepare_resume(conversation['id'])['unfinished'] == []
