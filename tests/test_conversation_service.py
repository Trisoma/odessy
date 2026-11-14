"""Conversation service: continuation truth, serialization, adoption, settlement."""
import asyncio
import uuid

import pytest

from odessy.application.conversations import (Conflict, ConversationStore,
                                              ConversationService, StorageError,
                                              Unsupported, build_service)
from odessy.application.conversations.lifecycle import (ConversationRecorder,
                                                        adopt_live_session,
                                                        settle_after_restart)


def settings(engine='legacy', profile='default'):
    return lambda: {'engine': engine, 'profile': profile}


def new_service(tmp_path, *, continuation=None, engine='legacy'):
    return build_service(path=tmp_path / 'conversations.sqlite3',
                         continuation=continuation,
                         settings=settings(engine))


def project(service):
    return service.create_project('Work')


def test_engine_metadata_and_continuation_classification(tmp_path):
    harness = new_service(tmp_path / 'h', engine='harness')
    legacy = new_service(tmp_path / 'l')
    assert harness.create(project_id=project(harness)['id'])['continuation'] == 'history_only'
    row = legacy.create(project_id=project(legacy)['id'])
    assert row['engine'] == 'legacy' and row['profile'] == 'default'
    assert row['continuation'] == 'history_only'  # no continuation owner bound yet
    with_owner = new_service(tmp_path / 'w',
                             continuation=lambda prepared: {'session_id': 's'})
    assert with_owner.create(project_id=project(with_owner)['id'])[
        'continuation'] == 'continuable'


def test_stable_ids_survive_service_recreation(tmp_path):
    service = new_service(tmp_path)
    work = project(service)
    row = service.create(project_id=work['id'], title='Persisted')
    service.store.append(row['id'], event_id='u', kind='user', payload={'text': 'remember'})
    service.store.close()

    reopened = new_service(tmp_path)
    same = reopened.get(row['id'])
    assert same['title'] == 'Persisted' and same['revision'] == 1
    assert reopened.messages(row['id'])['messages'][0]['payload']['text'] == 'remember'
    assert reopened.list(project_id=work['id'])['conversations'][0]['id'] == row['id']


def test_rename_pin_archive_with_revision_guard(tmp_path):
    service = new_service(tmp_path)
    row = service.create(project_id=project(service)['id'])
    renamed = service.patch(row['id'], expected_revision=0, title='Renamed', pinned=True)
    assert renamed['title'] == 'Renamed' and renamed['pinned'] is True
    with pytest.raises(Conflict):
        service.patch(row['id'], expected_revision=0, title='Stale')
    archived = service.patch(row['id'], expected_revision=1, archived=True)
    assert archived['archived'] is True
    assert service.list(project_id=row['project_id'])['conversations'] == []
    assert service.list(archived=True)['conversations'][0]['id'] == row['id']


def test_delete_runs_wave2_cleanup_and_refuses_on_failure(tmp_path):
    service = new_service(tmp_path)
    row = service.create(project_id=project(service)['id'])
    calls = []
    service.register_before_delete(calls.append)
    service.delete(row['id'], expected_revision=0)
    assert calls == [row['id']]

    other = service.create(project_id=row['project_id'])
    service.store.register_before_delete(lambda conversation_id: (_ for _ in ()).throw(
        RuntimeError('derived index unavailable')))
    with pytest.raises(StorageError):
        service.delete(other['id'], expected_revision=0)
    assert service.get(other['id'])['id'] == other['id']


def test_resume_is_unsupported_without_owner_or_on_harness(tmp_path):
    legacy = new_service(tmp_path / 'legacy')
    row = legacy.create(project_id=project(legacy)['id'])
    with pytest.raises(Unsupported):
        asyncio.run(legacy.resume(row['id'], expected_revision=0))
    harness = new_service(tmp_path / 'harness', engine='harness')
    harness_row = harness.create(project_id=project(harness)['id'])
    with pytest.raises(Unsupported):
        asyncio.run(harness.resume(harness_row['id'], expected_revision=0))


def test_resume_rejects_stale_revision_before_creating_a_session(tmp_path):
    calls = []

    async def continuation(prepared):
        calls.append(prepared)
        return {'session_id': 'live-1'}

    service = new_service(tmp_path, continuation=continuation)
    row = service.create(project_id=project(service)['id'])
    service.store.append(row['id'], event_id='u', kind='user', payload={'text': 'hello'})
    with pytest.raises(Conflict):
        asyncio.run(service.resume(row['id'], expected_revision=0))
    assert calls == []


def test_resume_returns_one_live_session_and_serializes_others(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()
    prepared_rows = []

    async def continuation(prepared):
        prepared_rows.append(prepared)
        started.set()
        await release.wait()
        return {'session_id': 'live-2'}

    service = new_service(tmp_path, continuation=continuation)
    row = service.create(project_id=project(service)['id'])
    service.store.append(row['id'], event_id='u', kind='user', payload={'text': 'hello'})
    service.store.append(row['id'], event_id='a', kind='assistant',
                         payload={'text': 'welcome back'})

    async def scenario():
        first = asyncio.create_task(service.resume(row['id'], expected_revision=2))
        await started.wait()
        with pytest.raises(Conflict):
            await service.resume(row['id'], expected_revision=2)
        release.set()
        return await first

    result = asyncio.run(scenario())
    assert result['continuation'] == 'continuable'
    assert result['session_id'] == 'live-2'
    assert result['service_instance_id'] == ''
    assert len(prepared_rows) == 1
    assert prepared_rows[0]['turns'] == [{'user': 'hello', 'assistant': 'welcome back'}]


def test_adoption_policy_is_idempotent_and_keeps_live_turn(tmp_path):
    service = new_service(tmp_path)
    work = project(service)
    turns = [{'user': 'first question', 'assistant': 'first answer'}]
    adopted = adopt_live_session(service, project_id=work['id'], session_id='live-session',
                                 engine='legacy', profile='default', turns=turns)
    again = adopt_live_session(service, project_id=work['id'], session_id='live-session',
                               engine='legacy', profile='default', turns=turns)
    assert adopted['id'] == again['id']
    assert len(service.messages(adopted['id'])['messages']) == 2

    recorder = ConversationRecorder(service, adopted['id'], session_id='live-session')
    recorder.begin_turn('second question')
    recorder.delta('partial answer')
    assert len(service.messages(adopted['id'])['messages']) == 3
    assert service.prepare_resume(adopted['id'])['open_user'] == 'second question'


def test_restart_settlement_marks_unknown_outcomes_once(tmp_path):
    service = new_service(tmp_path)
    row = service.create(project_id=project(service)['id'])
    recorder = ConversationRecorder(service, row['id'], session_id='s1')
    recorder.begin_turn('do the thing')
    recorder.delta('partial ')
    recorder.delta('answer')
    recorder.tool_call('call-1', 'read_file')

    assert settle_after_restart(service.store) == 2
    assert settle_after_restart(service.store) == 0  # idempotent after another restart
    kinds = [event['kind'] for event in service.messages(row['id'])['messages']]
    assert kinds == ['user', 'tool_call', 'interrupted', 'interrupted']
    interrupted = [event for event in service.messages(row['id'])['messages']
                   if event['kind'] == 'interrupted']
    assert interrupted[0]['payload']['tool_calls'] == ['call-1']
    assert interrupted[1]['payload']['outcome'] == 'unknown'
    assert interrupted[1]['payload']['text'] == 'partial answer'
    prepared = service.prepare_resume(row['id'])
    assert prepared['turns'] == []
    assert [item['kind'] for item in prepared['unfinished']] == [
        'tool_call', 'interrupted', 'interrupted']


def test_recorder_redacts_before_persisting_and_fails_closed(tmp_path):
    service = new_service(tmp_path)
    row = service.create(project_id=project(service)['id'])
    recorder = ConversationRecorder(service, row['id'])
    recorder.begin_turn('OPENAI_API_KEY=sk-abcdef123456 please hide')
    stored = service.messages(row['id'])['messages'][0]['payload']['text']
    assert 'sk-abcdef123456' not in stored and '<redacted>' in stored
    recorder.settle('done')
    assert [event['kind'] for event in service.messages(row['id'])['messages']] == [
        'user', 'assistant']

    def fault(operation):
        if operation == 'append':
            raise OSError('disk full')

    broken = ConversationService(ConversationStore(tmp_path / 'broken.sqlite3', fault=fault))
    second = broken.create(project_id=broken.create_project('Work')['id'])
    with pytest.raises(StorageError):
        ConversationRecorder(broken, second['id']).begin_turn('must not dispatch')


def test_log_maps_live_events_to_durable_records(tmp_path):
    from odessy.application.conversations.lifecycle import ConversationLog

    service = new_service(tmp_path)
    row = service.create(project_id=project(service)['id'])
    recorder = ConversationRecorder(service, row['id'])
    log = ConversationLog(recorder)
    recorder.begin_turn('read the file')
    log.observe('transcript', {'role': 'user', 'text': 'read the file'})
    log.observe('transcript', {'role': 'assistant', 'text': 'let me look'})
    log.observe('tool', {'phase': 'started', 'call_id': 'call-1', 'name': 'read_file'})
    log.observe('tool', {'phase': 'finished', 'call_id': 'call-1', 'name': 'read_file',
                         'status': 'ok'})
    assert [event['kind'] for event in service.messages(row['id'])['messages']] == [
        'user', 'tool_call', 'tool_result']
    assert service.prepare_resume(row['id'])['unfinished'] == []


def test_export_shape_and_search_across_conversations(tmp_path):
    service = new_service(tmp_path)
    work = project(service)
    first = service.create(project_id=work['id'], title='Alpha')
    second = service.create(project_id=work['id'], title='Beta')
    service.store.append(first['id'], event_id='u', kind='user',
                         payload={'text': 'find the compactor'})
    service.store.append(second['id'], event_id='u', kind='user',
                         payload={'text': 'unrelated note'})
    found = service.search(query='compactor')
    assert found['matches'][0]['conversation_id'] == first['id']
    document = service.export(first['id'])
    assert document['conversation']['title'] == 'Alpha'
    assert [event['kind'] for event in document['events']] == ['user']
    assert uuid.UUID(document['conversation']['id']).hex == first['id']
