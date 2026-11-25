"""Execution breakpoints: sanitized payloads, idempotence, listing, is_critical."""
import asyncio
import uuid

import pytest

from odessy.application.breakpoints import BreakpointHook, BreakpointService, is_critical
from odessy.application.breakpoints.policy import SIDE_EFFECT_TOOLS
from odessy.application.conversations.store import Conflict, ConversationStore, Invalid


def make_store(tmp_path):
    return ConversationStore(tmp_path / 'conversations.sqlite3')


def seed(store, title='First'):
    project = store.create_project('Work')
    row = store.create(conversation_id=uuid.uuid4().hex, project_id=project['id'],
                       engine='legacy', profile='default', title=title)
    return row


class FixedClock:
    def __init__(self, value='2026-11-23T08:00:00Z'):
        self.value = value
    def __call__(self):
        return self.value


def test_is_critical_whitelist_risk_and_external():
    for name in SIDE_EFFECT_TOOLS:
        assert is_critical(name, {}, None) is True
    assert is_critical('filesystem.read', {}, None) is False
    assert is_critical('calculator.eval', {}, None) is False
    high = type('Decision', (), {'risk': 'high'})()
    assert is_critical('some_new_builtin', {}, high, external=False) is True
    low = type('Decision', (), {'risk': 'low'})()
    assert is_critical('some_new_builtin', {}, low, external=False) is False
    # Configured external tools are always critical.
    assert is_critical('mcp.custom_action', {}, None) is True
    assert is_critical('', {}, None) is False


def test_record_breakpoint_is_sanitized_and_excluded_from_messages(tmp_path):
    store = make_store(tmp_path)
    conversation = seed(store)
    service = BreakpointService(store, clock=FixedClock())
    event = service.record(
        conversation['id'], kind='critical-before', session_id='a' * 32,
        user_prompt='please write the file',
        tool={
            'name': 'filesystem.write', 'risk': 'high', 'policy_outcome': 'confirm',
            'status': 'pending',
            'arguments': {'path': 'src/a.py', 'content': 'TOP_SECRET_VALUE_12345',
                          'api_key': 'sk-abcdefghijklmnop'},
        },
        cursor={'first_event_sequence': 1, 'last_event_sequence': 4},
    )
    payload = store.event_by_id(conversation['id'], event['id'])['payload']
    projection = payload['tool']['arguments_projection']
    # Values are never stored verbatim; only types/lengths/digests/excerpts.
    assert 'TOP_SECRET_VALUE_12345' not in str(payload)
    assert projection['content']['type'] == 'string'
    assert projection['content']['len'] == len('TOP_SECRET_VALUE_12345')
    assert projection['api_key']['sha256']
    # Breakpoints never surface as messages.
    assert [m['kind'] for m in store.messages(conversation['id'])['messages']] == []
    # But the timeline path includes them.
    assert store.breakpoints(conversation['id'])[0]['event_id'] == event['id']


def test_breakpoint_ids_are_idempotent_via_store(tmp_path):
    store = make_store(tmp_path)
    conversation = seed(store)
    service = BreakpointService(store, clock=FixedClock())
    event = service.record(conversation['id'], kind='step', session_id='b' * 32)
    rev = store.get(conversation['id'])['revision']
    original = store.event_by_id(conversation['id'], event['id'])
    # Replaying the same event id cannot duplicate history.
    again = store.append(conversation['id'], event_id=event['id'], kind='breakpoint',
                         payload=original['payload'])
    assert again['event_id'] == original['event_id']
    assert again['sequence'] == original['sequence']
    assert store.get(conversation['id'])['revision'] == rev


def test_hook_before_after_and_task_anchor(tmp_path):
    store = make_store(tmp_path)
    conversation = seed(store)
    service = BreakpointService(store, clock=FixedClock())
    ctx = {'turn_id': 't1', 'user_prompt': 'do the thing', 'last_sequence': 2,
           'files_sha256': {'src/a.py': 'deadbeef'}}
    hook = BreakpointHook(service, conversation_id=conversation['id'],
                          session_id='c' * 32, get_context=lambda: ctx)
    start = hook.begin_turn('t1', 'do the thing')
    assert start['kind'] == 'task-start'
    assert start['task']['turn_id'] == 't1'
    assert hook._task_start_breakpoint == start['id']
    decision = type('Decision', (), {'risk': 'high', 'action': 'confirm'})()
    before = hook.before_tool(name='filesystem.write', arguments={'path': 'src/a.py'},
                              decision=decision, call_id='call-1')
    assert before['kind'] == 'critical-before'
    assert before['resume']['undo_boundaries'] == [start['id']]
    after = hook.after_tool(name='filesystem.write', arguments={'path': 'src/a.py'},
                            decision=decision, call_id='call-1', status='ok')
    assert after['kind'] == 'critical-after'
    assert after['completed']['ok_tool_calls'] == ['call-1']
    step = hook.step()
    assert step['kind'] == 'step'
    assert hook._task_start_breakpoint == start['id']
    # task-start + critical-before + critical-after + step.
    assert len(store.breakpoints(conversation['id'])) == 4


def test_hook_non_critical_tool_writes_nothing(tmp_path):
    store = make_store(tmp_path)
    conversation = seed(store)
    service = BreakpointService(store, clock=FixedClock())
    hook = BreakpointHook(service, conversation_id=conversation['id'], session_id='d' * 32)
    assert hook.before_tool(name='filesystem.read', arguments={'path': 'x'}, call_id='r1') is None
    assert store.breakpoints(conversation['id']) == ()


def test_undo_marks_history_without_deleting(tmp_path):
    store = make_store(tmp_path)
    conversation = seed(store)
    service = BreakpointService(store, clock=FixedClock())
    store.append(conversation['id'], event_id='u1', kind='user', payload={'text': 'q'})
    anchor = service.record(conversation['id'], kind='critical-before', session_id='e' * 32,
                            tool={'name': 'filesystem.write', 'call_id': 'call-1'},
                            cursor={'first_event_sequence': 1, 'last_event_sequence': 2})
    store.append(conversation['id'], event_id='a1', kind='assistant', payload={'text': 'a'})
    revision = store.get(conversation['id'])['revision']
    result = service.undo(conversation['id'], anchor['id'], expected_revision=revision,
                          session_id='e' * 32)
    assert result['undo_of'] == anchor['id']
    assert result['history_preserved'] is True
    # Original events still present; undo is append-only.
    kinds = [e['kind'] for e in store.raw_events(conversation['id'])]
    assert 'user' in kinds and 'assistant' in kinds and 'undo' in kinds


def test_sequence_bounds_is_lightweight_and_correct(tmp_path):
    store = make_store(tmp_path)
    conversation = seed(store)
    assert store.sequence_bounds(conversation['id']) == (0, 0)
    store.append(conversation['id'], event_id='u1', kind='user', payload={'text': 'q'})
    store.append(conversation['id'], event_id='a1', kind='assistant', payload={'text': 'a'})
    assert store.sequence_bounds(conversation['id']) == (1, 2)


def test_hook_timer_breakpoint_respects_interval(tmp_path):
    store = make_store(tmp_path)
    conversation = seed(store)
    service = BreakpointService(store, clock=FixedClock())
    clock = {'now': 0.0}
    hook = BreakpointHook(service, conversation_id=conversation['id'], session_id='9' * 32,
                          timer_seconds=60.0, clock=lambda: clock['now'])
    # First tick before the interval: no write.
    assert hook.tick() is None
    clock['now'] = 61.0
    result = hook.tick()
    assert result is not None and result['kind'] == 'timer'
    # A write resets the clock; immediate tick does nothing.
    assert hook.tick() is None


def test_settle_after_restart_reports_interrupts(tmp_path):
    from odessy.application.conversations.lifecycle import settle_after_restart
    store = make_store(tmp_path)
    conversation = seed(store)
    store.append(conversation['id'], event_id='u1', kind='user', payload={'text': 'q'})
    store.append(conversation['id'], event_id='tc', kind='tool_call',
                 payload={'call_id': 'open-call', 'name': 'filesystem.write'})
    seen = []
    count = settle_after_restart(store, on_interrupt=lambda cid, reason, details: seen.append((cid, reason)))
    assert count >= 1
    assert any(reason == 'restart:tools' for _, reason in seen)


def test_undo_rejects_conflict_and_unknown_anchor(tmp_path):
    store = make_store(tmp_path)
    conversation = seed(store)
    service = BreakpointService(store, clock=FixedClock())
    with pytest.raises(Invalid):  # malformed breakpoint id
        service.undo(conversation['id'], 'not-a-bp', expected_revision=0)
    anchor = service.record(conversation['id'], kind='step', session_id='f' * 32)
    with pytest.raises(Conflict):  # revision mismatch
        service.undo(conversation['id'], anchor['id'], expected_revision=0)


class _FakeCheckpoints:
    """Records what revision undo_async passes to restore; review is a no-op."""
    def __init__(self, checkpoint_revision):
        self.checkpoint_revision = checkpoint_revision
        self.restore_revisions = []

    def preview(self, sid, cid):
        return {'preview_hash': 'a' * 64,
                'expected_revision': self.checkpoint_revision,
                'changes': []}

    async def run_async(self, function, *args, **kwargs):
        return function(*args, **kwargs)

    def restore(self, sid, cid, *, preview_hash, expected_revision):
        self.restore_revisions.append(expected_revision)
        return {'safety_checkpoint_id': 'ckpt_safety'}


def test_undo_async_passes_checkpoint_revision_not_conversation_revision(tmp_path):
    """Regression: restore validates the checkpoint revision, never the conversation one.

    The two are independent counters. Passing the conversation revision makes
    CheckpointService.restore raise 409 and file rollback never happens.
    """
    store = make_store(tmp_path)
    conversation = seed(store)
    service = BreakpointService(store, clock=FixedClock())
    checkpoints = _FakeCheckpoints(checkpoint_revision=3)
    service.checkpoints = checkpoints

    anchor = service.record(
        conversation['id'], kind='critical-before', session_id='e' * 32,
        workspace={'snapshot_ref': 'refs/odessy/checkpoints/ckpt_' + '0' * 32},
        cursor={'first_event_sequence': 1, 'last_event_sequence': 1})
    revision = store.get(conversation['id'])['revision']
    # Make the two counters clearly different so a mix-up cannot pass by accident.
    assert revision != checkpoints.checkpoint_revision

    result = asyncio.run(service.undo_async(
        conversation['id'], anchor['id'], expected_revision=revision, session_id='e' * 32))

    assert checkpoints.restore_revisions == [checkpoints.checkpoint_revision]
    assert result['file_rollback']['status'] == 'restored'
