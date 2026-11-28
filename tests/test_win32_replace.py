"""Windows handle-anchored replacement: correctness and path-replacement refusal."""
from __future__ import annotations

import os
import subprocess

import pytest

from odessy.application.checkpoints import win32_replace as wr


needs_nt = pytest.mark.skipif(os.name != 'nt', reason='Windows-only NT API bindings')
needs_git = pytest.mark.skipif(
    not (lambda: (subprocess.run(['git', '--version'], capture_output=True, timeout=20)
                  .returncode == 0))(),
    reason='git not installed')


@needs_nt
def test_available_on_windows():
    assert wr.available() is True


@needs_nt
def test_replace_anchored_writes_file(tmp_path):
    root = tmp_path / 'ws'
    (root / 'sub').mkdir(parents=True)
    target = root / 'sub' / 'f.txt'
    target.write_text('old', encoding='utf-8')

    wr.replace_anchored(str(root), 'sub/f.txt', b'RESTORED')

    assert target.read_bytes() == b'RESTORED'
    assert list((root / 'sub').glob('.ckpt-*')) == []


@needs_nt
def test_replace_anchored_creates_missing_target(tmp_path):
    root = tmp_path / 'ws'
    root.mkdir()
    wr.replace_anchored(str(root), 'new.txt', b'created')
    assert (root / 'new.txt').read_bytes() == b'created'


@needs_nt
def test_replace_anchored_rejects_traversal(tmp_path):
    root = tmp_path / 'ws'
    root.mkdir()
    with pytest.raises(wr.WindowsReplaceError):
        wr.replace_anchored(str(root), '../escape.txt', b'x')


@needs_nt
def test_replace_anchored_refuses_reparse_component(tmp_path):
    """A junction in the path must be refused before any target write."""
    root = tmp_path / 'ws'
    (root / 'sub').mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'f.txt').write_text('do not touch', encoding='utf-8')

    junction = root / 'jump'
    made = subprocess.run(
        ['cmd', '/c', 'mklink', '/J', str(junction), str(outside)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    if made.returncode != 0:
        pytest.skip(f'cannot create junction on this host: {made.stderr.strip()}')

    try:
        with pytest.raises(wr.WindowsReplaceError, match='reparse'):
            wr.replace_anchored(str(root), 'jump/f.txt', b'OVERWRITE')
        # The file outside the workspace is untouched.
        assert (outside / 'f.txt').read_text(encoding='utf-8') == 'do not touch'
    finally:
        subprocess.run(['cmd', '/c', 'rmdir', str(junction)],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)


@needs_nt
def test_replace_anchored_rejects_when_target_drifted(tmp_path):
    """An approval hash that no longer matches the anchored target is refused."""
    import hashlib
    root = tmp_path / 'ws'
    root.mkdir()
    target = root / 'f.txt'
    target.write_bytes(b'old')
    stale = hashlib.sha256(b'different').hexdigest()
    with pytest.raises(wr.WindowsReplaceError, match='changed after approval'):
        wr.replace_anchored(str(root), 'f.txt', b'new', expected_sha256=stale)
    assert target.read_bytes() == b'old'


@needs_nt
def test_end_to_end_undo_restores_file_on_windows(tmp_path):
    """Full chain: auto snapshot -> breakpoint -> undo_async -> anchored restore."""
    import asyncio
    import uuid

    from odessy.application.breakpoints import BreakpointHook, BreakpointService
    from odessy.application.checkpoints.service import CheckpointService
    from odessy.application.conversations.store import ConversationStore
    from test_checkpoint_service import Binding, Grants, Inspector, git

    root = tmp_path / 'ws'
    root.mkdir()
    (root / 'readme.txt').write_text('baseline\n', encoding='utf-8')
    subprocess.run(['git', 'init', '-q', str(root)], check=True, timeout=60)
    git(root, 'config', 'user.email', 't@example.test')
    git(root, 'config', 'user.name', 'T')
    git(root, 'add', '--', 'readme.txt')
    git(root, 'commit', '-q', '-m', 'base')
    binding = Binding(root)

    checkpoints = CheckpointService(inspector=Inspector(binding), request_grant=Grants(),
                                    state_dir=tmp_path / 'state')
    store = ConversationStore(tmp_path / 'conv.sqlite3')
    project = store.create_project('Work')
    conv = store.create(conversation_id=uuid.uuid4().hex, project_id=project['id'],
                        engine='legacy', profile='default', title='t')
    bp_service = BreakpointService(store, checkpoints=checkpoints)

    hook = BreakpointHook(bp_service, conversation_id=conv['id'], session_id=binding.session_id,
                          snapshot_fn=lambda reason: checkpoints.auto_snapshot(binding.session_id, reason))
    start = hook.begin_turn('t1', 'edit readme')

    # Simulate the task modifying the file after the baseline snapshot.
    (root / 'readme.txt').write_text('task edit\n', encoding='utf-8')
    assert (root / 'readme.txt').read_text(encoding='utf-8') == 'task edit\n'

    revision = store.get(conv['id'])['revision']
    result = asyncio.run(bp_service.undo_async(
        conv['id'], start['id'], expected_revision=revision, session_id=binding.session_id))

    assert result['file_rollback']['status'] == 'restored'
    assert (root / 'readme.txt').read_text(encoding='utf-8') == 'baseline\n'


@needs_nt
def test_replacement_checks_open_root_identity(tmp_path):
    old, current = tmp_path / 'old', tmp_path / 'current'
    old.mkdir()
    current.mkdir()
    expected = old.stat()
    with pytest.raises(wr.WindowsReplaceError, match='workspace root changed'):
        wr.replace_anchored(str(current), 'new.txt', b'no',
                            root_identity=(expected.st_dev, expected.st_ino))
    assert not list(current.iterdir())


@needs_nt
def test_replacement_refuses_target_that_appeared_since_approval(tmp_path):
    target = tmp_path / 'appeared.txt'
    target.write_bytes(b'keep this')
    root = tmp_path.stat()
    with pytest.raises(wr.WindowsReplaceError, match='changed after approval'):
        wr.replace_anchored(str(tmp_path), target.name, b'no', expected_sha256=None,
                            root_identity=(root.st_dev, root.st_ino))
    assert target.read_bytes() == b'keep this'
    assert not list(tmp_path.glob('.ckpt-*'))
