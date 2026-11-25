"""Automatic, deduplicated workspace snapshots bound to execution breakpoints."""
from __future__ import annotations

import subprocess
import uuid

import pytest

from odessy.application.breakpoints import BreakpointHook, BreakpointService
from odessy.application.checkpoints.service import CheckpointService
from odessy.application.conversations.store import ConversationStore
from test_checkpoint_service import Binding, Grants, Inspector, git


def needs_git() -> bool:
    try:
        subprocess.run(['git', '--version'], capture_output=True, timeout=20, check=True)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not needs_git(), reason='git is not installed')


def make_ws(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'readme.txt').write_text('Before\n', encoding='utf-8')
    subprocess.run(['git', 'init', '-q', str(root)], check=True, timeout=60)
    git(root, 'config', 'user.email', 'fixture@example.test')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'add', '--', 'readme.txt')
    git(root, 'commit', '-q', '-m', 'fixture base')
    return root, Binding(root)


def make_checkpoints(tmp_path, binding):
    return CheckpointService(inspector=Inspector(binding), request_grant=Grants(),
                             state_dir=tmp_path / 'state')


def test_auto_snapshot_dedups_unchanged_workspace(tmp_path):
    root, binding = make_ws(tmp_path)
    api = make_checkpoints(tmp_path, binding)
    first = api.auto_snapshot(binding.session_id, 'task start')
    assert first and not first['deduplicated'] and first['ref'].startswith('refs/odessy/checkpoints/')
    # Same bytes: reuse, no new ref.
    again = api.auto_snapshot(binding.session_id, 'step 1')
    assert again and again['deduplicated'] is True
    assert again['id'] == first['id']
    assert len(api.list(binding.session_id)['checkpoints']) == 1
    # Change a file: a new snapshot is captured and bound to a new ref.
    (root / 'readme.txt').write_text('After\n', encoding='utf-8')
    third = api.auto_snapshot(binding.session_id, 'after write')
    assert third and third['deduplicated'] is False and third['id'] != first['id']
    assert len(api.list(binding.session_id)['checkpoints']) == 2


def test_auto_snapshot_is_approval_free_and_best_effort(tmp_path):
    root, binding = make_ws(tmp_path)
    grants = Grants(approve=False)
    api = CheckpointService(inspector=Inspector(binding), request_grant=grants,
                            state_dir=tmp_path / 'state')
    # Snapshots are observation-only: no broker approval is ever requested.
    assert api.auto_snapshot(binding.session_id, 'task start') is not None
    assert grants.calls == []
    # A non-Git workspace yields None instead of raising.
    (tmp_path / 'plain').mkdir()
    (tmp_path / 'plain' / 'a.txt').write_text('x', encoding='utf-8')
    plain_binding = Binding(tmp_path / 'plain')
    plain_api = CheckpointService(inspector=Inspector(plain_binding), request_grant=Grants(),
                                  state_dir=tmp_path / 'plain-state')
    assert plain_api.auto_snapshot(plain_binding.session_id, 'task start') is None


def test_hook_captures_snapshot_at_every_key_moment(tmp_path):
    root, binding = make_ws(tmp_path)
    api = make_checkpoints(tmp_path, binding)
    calls = []

    def snapshot_fn(reason):
        calls.append(reason)
        return api.auto_snapshot(binding.session_id, reason)

    store = ConversationStore(tmp_path / 'conversations.sqlite3')
    project = store.create_project('Work')
    conversation = store.create(conversation_id=uuid.uuid4().hex, project_id=project['id'],
                                engine='legacy', profile='default', title='First')
    service = BreakpointService(store)
    hook = BreakpointHook(service, conversation_id=conversation['id'], session_id=binding.session_id,
                          snapshot_fn=snapshot_fn, timer_seconds=0)
    hook.begin_turn('t1', 'do the thing')
    decision = type('Decision', (), {'risk': 'high', 'action': 'confirm'})()
    hook.before_tool(name='filesystem.write', arguments={'path': 'readme.txt'}, decision=decision,
                     call_id='c1')
    hook.after_tool(name='filesystem.write', arguments={'path': 'readme.txt'}, decision=decision,
                    call_id='c1', status='ok')
    hook.step()
    hook.timer()

    # Every trigger point captured (dedup means only one real Git ref exists).
    assert [c.split()[0] for c in calls] == ['task', 'before', 'after', 'step', 'idle']
    assert len(api.list(binding.session_id)['checkpoints']) == 1
    # The task-start breakpoint carries the snapshot ref for undo.
    events = store.breakpoints(conversation['id'])
    start = [e for e in events if e['payload']['kind'] == 'task-start'][0]
    assert start['payload']['workspace']['snapshot_ref'].startswith('refs/odessy/checkpoints/')


def test_breakpoints_track_file_changes_after_edit(tmp_path):
    root, binding = make_ws(tmp_path)
    api = make_checkpoints(tmp_path, binding)
    store = ConversationStore(tmp_path / 'conversations.sqlite3')
    project = store.create_project('Work')
    conversation = store.create(conversation_id=uuid.uuid4().hex, project_id=project['id'],
                                engine='legacy', profile='default', title='First')
    service = BreakpointService(store)
    hook = BreakpointHook(service, conversation_id=conversation['id'], session_id=binding.session_id,
                          snapshot_fn=lambda reason: api.auto_snapshot(binding.session_id, reason))
    hook.begin_turn('t1', 'edit the file')
    before_hash = hook._auto_snapshot_files['readme.txt']
    (root / 'readme.txt').write_text('After\n', encoding='utf-8')
    hook.before_tool(name='filesystem.write', arguments={'path': 'readme.txt'},
                     decision=type('Decision', (), {'risk': 'high'})(), call_id='c1')
    assert hook._auto_snapshot_files['readme.txt'] != before_hash
    assert len(api.list(binding.session_id)['checkpoints']) == 2
