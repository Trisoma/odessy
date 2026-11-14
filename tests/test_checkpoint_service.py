"""B14 checkpoints: real Git snapshots, previewed restore, safety and preservation."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from odessy.application.checkpoints import service as checkpoint_module
from odessy.application.checkpoints.service import CheckpointError, CheckpointService
from odessy.authority.policy import OdessyPolicy

pytestmark: list = []


def shutil_git() -> bool:
    try:
        subprocess.run(['git', '--version'], capture_output=True, timeout=20, check=True)
        return True
    except Exception:
        return False


needs_git = pytest.mark.skipif(not shutil_git(), reason='git is not installed')


@pytest.mark.parametrize('payload', [None, b'', b'exact reviewed blob\r\n'])
def test_git_never_inherits_coordinator_stdin(tmp_path, monkeypatch, payload):
    """An installed service keeps stdin open; only explicit blob bytes reach Git."""
    seen = []

    def run(command, **kwargs):
        seen.append(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout=b'result', stderr=b'')

    monkeypatch.setattr(checkpoint_module.subprocess, 'run', run)
    api = CheckpointService(inspector=None, request_grant=None, state_dir=tmp_path)
    assert api._git(tmp_path, 'hash-object', input_bytes=payload) == b'result'
    if payload is None:
        assert seen[0]['stdin'] == subprocess.DEVNULL
        assert 'input' not in seen[0]
    else:
        assert seen[0]['input'] == payload
        assert 'stdin' not in seen[0]


def git(root: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                          text=True, timeout=60, check=True).stdout


class Binding:
    def __init__(self, root: Path, *, authorized: bool = True) -> None:
        self.service_instance_id = 'svc-1'
        self.session_id = 's1'
        self.workspace_id = 'ws-1'
        self.label = 'fixture'
        self.local = True
        self.policy = OdessyPolicy(root)
        self.root_path = root
        info = root.stat()
        self.root_identity = (info.st_dev, info.st_ino)
        self.authorized = authorized
        self.checks = 0

    def identity(self):
        return (self.service_instance_id, self.session_id, self.workspace_id,
                str(self.root_path), self.root_identity, repr(self.policy.identity()))

    def is_authorized(self) -> bool:
        self.checks += 1
        return self.authorized


class Inspector:
    def __init__(self, binding: Binding | None) -> None:
        self.binding_value = binding

    def binding(self, sid):
        if self.binding_value is None or sid != self.binding_value.session_id:
            raise LookupError('no owner')
        return self.binding_value


class Grants:
    def __init__(self, approve: bool = True) -> None:
        self.approve = approve
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, sid: str, plan: dict) -> bool:
        self.calls.append((sid, plan))
        return self.approve


def workspace(tmp_path: Path, *, git_repo: bool = True) -> tuple[Path, Binding]:
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'readme.txt').write_text('Before\n', encoding='utf-8')
    (root / 'notes.md').write_text('# Notes\n', encoding='utf-8')
    if git_repo:
        subprocess.run(['git', 'init', '-q', str(root)], check=True, timeout=60)
        git(root, 'config', 'user.email', 'fixture@example.test')
        git(root, 'config', 'user.name', 'Fixture')
        git(root, 'add', '--', 'readme.txt', 'notes.md')
        git(root, 'commit', '-q', '-m', 'fixture base')
    return root, Binding(root)


def service(tmp_path: Path, binding: Binding, grants: Grants | None = None,
            state: Path | None = None) -> CheckpointService:
    return CheckpointService(
        inspector=Inspector(binding),
        request_grant=grants if grants is not None else Grants(),
        state_dir=state or (tmp_path / 'state'),
    )


@needs_git
def test_create_uses_owned_refs_and_never_touches_user_git_state(tmp_path):
    root, binding = workspace(tmp_path)
    before_head = git(root, 'rev-parse', 'HEAD').strip()
    before_branch = git(root, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
    before_staged = git(root, 'diff', '--cached', '--name-only')
    grants = Grants()
    api = service(tmp_path, binding, grants)

    created = api.create('s1', 'before edit')

    assert created['version'] == 1 and created['revision'] == 1
    assert created['label'] == 'before edit' and created['files'] == 2
    assert git(root, 'rev-parse', 'HEAD').strip() == before_head
    assert git(root, 'rev-parse', '--abbrev-ref', 'HEAD').strip() == before_branch
    assert git(root, 'diff', '--cached', '--name-only') == before_staged
    refs = git(root, 'for-each-ref', '--format=%(refname)', 'refs/odessy/checkpoints')
    assert f'refs/odessy/checkpoints/{created["id"]}' in refs
    assert grants.calls and grants.calls[0][1]['action'] == 'checkpoint.create'
    # The user's working tree is untouched by creating a snapshot.
    assert (root / 'readme.txt').read_text(encoding='utf-8') == 'Before\n'


@needs_git
def test_preview_and_restore_recover_files_and_take_a_safety_snapshot(tmp_path):
    root, binding = workspace(tmp_path)
    api = service(tmp_path, binding)
    created = api.create('s1', 'baseline')

    (root / 'readme.txt').write_text('After\n', encoding='utf-8')
    (root / 'notes.md').unlink()
    preview = api.preview('s1', created['id'])
    statuses = {change['path']: change['status'] for change in preview['changes']}
    assert statuses == {'readme.txt': 'modified', 'notes.md': 'missing'}
    assert preview['expected_revision'] == 1
    assert len(preview['preview_hash']) == 64

    restored = api.restore('s1', created['id'], preview_hash=preview['preview_hash'],
                           expected_revision=preview['expected_revision'])

    assert restored['status'] == 'restored'
    assert restored['safety_checkpoint_id'].startswith('ckpt_')
    assert (root / 'readme.txt').read_text(encoding='utf-8') == 'Before\n'
    assert (root / 'notes.md').read_text(encoding='utf-8') == '# Notes\n'
    listed = api.list('s1')
    kinds = [entry['label'] for entry in listed['checkpoints']]
    assert kinds[0] == 'baseline'
    assert any(label.startswith('pre-restore') for label in kinds)


@needs_git
def test_restore_refuses_drift_after_the_preview_and_stale_revisions(tmp_path):
    root, binding = workspace(tmp_path)
    api = service(tmp_path, binding)
    created = api.create('s1', 'baseline')
    (root / 'readme.txt').write_text('Changed once\n', encoding='utf-8')
    preview = api.preview('s1', created['id'])
    (root / 'readme.txt').write_text('Changed twice\n', encoding='utf-8')

    with pytest.raises(CheckpointError) as drift:
        api.restore('s1', created['id'], preview_hash=preview['preview_hash'],
                    expected_revision=preview['expected_revision'])
    assert drift.value.status == 409
    assert (root / 'readme.txt').read_text(encoding='utf-8') == 'Changed twice\n'

    (root / 'readme.txt').write_text('Changed once\n', encoding='utf-8')
    with pytest.raises(CheckpointError) as stale:
        api.restore('s1', created['id'], preview_hash=preview['preview_hash'],
                    expected_revision=7)
    assert stale.value.status == 409


@needs_git
def test_restore_preserves_unrelated_untracked_work_and_sensitive_files(tmp_path):
    root, binding = workspace(tmp_path)
    (root / '.env').write_text('TOKEN=secret\n', encoding='utf-8')
    api = service(tmp_path, binding)
    created = api.create('s1', 'baseline')
    entry = api._entries(binding)[created['id']]
    assert '.env' in entry['unsupported']
    assert '.env' not in entry['files_sha256']

    (root / 'scratch.log').write_text('unrelated\n', encoding='utf-8')
    (root / '.env').write_text('TOKEN=changed\n', encoding='utf-8')
    (root / 'readme.txt').write_text('After\n', encoding='utf-8')

    preview = api.preview('s1', created['id'])
    api.restore('s1', created['id'], preview_hash=preview['preview_hash'],
                expected_revision=preview['expected_revision'])

    assert (root / 'readme.txt').read_text(encoding='utf-8') == 'Before\n'
    assert (root / 'scratch.log').read_text(encoding='utf-8') == 'unrelated\n'
    # Sensitive content is never captured, so restore cannot revert it either.
    assert (root / '.env').read_text(encoding='utf-8') == 'TOKEN=changed\n'


@needs_git
def test_refused_or_unwired_grants_take_no_snapshot(tmp_path):
    root, binding = workspace(tmp_path)
    refused = Grants(approve=False)
    api = service(tmp_path, binding, refused)
    with pytest.raises(CheckpointError) as denied:
        api.create('s1', 'refused')
    assert denied.value.status == 403
    assert api.list('s1')['checkpoints'] == []

    unwired = CheckpointService(inspector=Inspector(binding),
                                request_grant=None, state_dir=tmp_path / 'state2')
    with pytest.raises(CheckpointError) as unsupported:
        unwired.create('s1', 'no grant')
    assert unsupported.value.status == 501
    assert git(root, 'for-each-ref', 'refs/odessy/checkpoints').strip() == ''


@needs_git
def test_revoked_binding_denies_every_operation(tmp_path):
    root, binding = workspace(tmp_path)
    api = service(tmp_path, binding)
    created = api.create('s1', 'baseline')
    binding.authorized = False
    for call in (lambda: api.list('s1'),
                 lambda: api.preview('s1', created['id']),
                 lambda: api.create('s1', 'blocked'),
                 lambda: api.restore('s1', created['id'], preview_hash='0' * 64,
                                     expected_revision=1)):
        with pytest.raises(CheckpointError) as error:
            call()
        assert error.value.status == 403


@needs_git
def test_checkpoints_persist_across_service_instances(tmp_path):
    root, binding = workspace(tmp_path)
    state = tmp_path / 'state'
    created = service(tmp_path, binding, state=state).create('s1', 'durable')
    reopened = service(tmp_path, binding, state=state)
    listed = reopened.list('s1')
    assert [entry['id'] for entry in listed['checkpoints']] == [created['id']]
    assert reopened.preview('s1', created['id'])['changes'][0]['status'] == 'unchanged'


@needs_git
def test_retention_prunes_owned_refs_only(tmp_path, monkeypatch):
    root, binding = workspace(tmp_path)
    api = service(tmp_path, binding)
    monkeypatch.setattr(checkpoint_module, 'RETENTION', 2)
    ids = [api.create('s1', f'step {index}')['id'] for index in range(4)]
    listed = api.list('s1')['checkpoints']
    assert [entry['id'] for entry in listed] == ids[-2:]
    refs = git(root, 'for-each-ref', '--format=%(refname)', 'refs/odessy/checkpoints')
    for old in ids[:-2]:
        assert f'refs/odessy/checkpoints/{old}' not in refs
    # Pruning snapshots never deletes workspace files.
    assert (root / 'readme.txt').exists()


@needs_git
def test_non_git_workspace_reports_unsupported_instead_of_faking_history(tmp_path):
    root, binding = workspace(tmp_path, git_repo=False)
    api = service(tmp_path, binding)
    with pytest.raises(CheckpointError) as unsupported:
        api.create('s1', 'nope')
    assert unsupported.value.status == 501
    assert 'Git workspace root' in unsupported.value.message
    assert api.list('s1')['checkpoints'] == []


@needs_git
def test_oversized_files_are_reported_not_silently_snapshotted(tmp_path, monkeypatch):
    root, binding = workspace(tmp_path)
    monkeypatch.setattr(checkpoint_module, 'MAX_FILE_BYTES', 4)
    api = service(tmp_path, binding)
    with pytest.raises(CheckpointError) as too_big:
        api.create('s1', 'tiny budget')
    assert too_big.value.status == 413
    assert api.list('s1')['checkpoints'] == []
    assert git(root, 'for-each-ref', 'refs/odessy/checkpoints').strip() == ''


@needs_git
def test_invalid_labels_ids_and_arguments_are_rejected(tmp_path):
    root, binding = workspace(tmp_path)
    api = service(tmp_path, binding)
    created = api.create('s1', 'baseline')
    for label, status in (('', 400), ('x' * 121, 400), ('bad\x01label', 400),
                          (None, 400), (7, 400)):
        with pytest.raises(CheckpointError) as error:
            api.create('s1', label)
        assert error.value.status == status
    with pytest.raises(CheckpointError) as bad_id:
        api.preview('s1', 'ckpt_short')
    assert bad_id.value.status == 404
    with pytest.raises(CheckpointError) as bad_hash:
        api.restore('s1', created['id'], preview_hash='nope', expected_revision=1)
    assert bad_hash.value.status == 400


@needs_git
def test_close_session_is_a_no_op_and_unknown_session_is_missing(tmp_path):
    root, binding = workspace(tmp_path)
    api = service(tmp_path, binding)
    api.create('s1', 'baseline')
    api.close_session('s1')
    assert api.list('s1')['checkpoints']
    with pytest.raises(CheckpointError) as missing:
        api.list('ghost')
    assert missing.value.status == 404


@needs_git
def test_create_refuses_grant_revocation_and_mode_change_during_approval(tmp_path):
    _, binding = workspace(tmp_path)
    revision = [0]
    def grant(sid, plan):
        revision[0] += 1
        return True
    api = CheckpointService(inspector=Inspector(binding), request_grant=grant,
                            state_dir=tmp_path / 'state',
                            mode_revision=lambda sid: revision[0])
    with pytest.raises(CheckpointError) as error:
        api.create('s1', 'stale mode')
    assert error.value.status == 409
    assert not api.list('s1')['checkpoints']
    api._grant = lambda sid, plan: setattr(binding, 'authorized', False) or True
    with pytest.raises(CheckpointError) as error:
        api.create('s1', 'revoked')
    assert error.value.status == 403


@needs_git
def test_checkpoint_metadata_survives_new_live_service_and_session(tmp_path):
    root, binding = workspace(tmp_path)
    api = service(tmp_path, binding)
    saved = api.create('s1', 'persistent')
    fresh = Binding(root)
    fresh.service_instance_id = 'svc-2'
    fresh.session_id = 's2'
    fresh.workspace_id = 'new-live-workspace-id'
    reopened = service(tmp_path, fresh)
    assert [row['id'] for row in reopened.list('s2')['checkpoints']] == [saved['id']]


@needs_git
def test_snapshot_preserves_raw_bytes_without_running_git_clean_filters(tmp_path):
    root, binding = workspace(tmp_path)
    git(root, 'config', 'core.autocrlf', 'true')
    (root / 'readme.txt').write_bytes(b'raw\nbytes\n')
    api = service(tmp_path, binding)
    saved = api.create('s1', 'raw bytes')
    blob = subprocess.run(['git', '-C', str(root), 'cat-file', 'blob',
                          f'{checkpoint_module.REF_PREFIX}{saved["id"]}:readme.txt'],
                         capture_output=True, check=True).stdout
    assert blob == b'raw\nbytes\n'
    git(root, 'config', 'filter.refuse.clean', 'a-program-that-does-not-exist')
    git(root, 'config', 'filter.refuse.required', 'true')
    (root / '.gitattributes').write_text('*.txt filter=refuse\n', encoding='utf-8')
    api.create('s1', 'do not execute filters')


@needs_git
def test_cancelled_async_approval_never_creates_checkpoint(tmp_path):
    import asyncio
    _, binding = workspace(tmp_path)
    async def run():
        waiting, ended = asyncio.Event(), asyncio.Event()
        async def grant(sid, plan):
            waiting.set()
            try:
                await asyncio.Event().wait()
            finally:
                ended.set()
        api = service(tmp_path, binding)
        api._grant = grant
        request = asyncio.create_task(api.run_async(api.create, 's1', 'cancelled'))
        await asyncio.wait_for(waiting.wait(), 5)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        await asyncio.wait_for(ended.wait(), 5)
        assert api.list('s1')['checkpoints'] == []
    asyncio.run(run())


@needs_git
def test_binary_and_runtime_database_are_excluded_and_preserved(tmp_path):
    root, binding = workspace(tmp_path)
    (root / 'binary.dat').write_bytes(b'\x00\xfforiginal')
    (root / 'conversations.sqlite3').write_bytes(b'SQLite format 3\x00\x80')
    api = service(tmp_path, binding)
    created = api.create('s1', 'text-only checkpoint')
    preview = api.preview('s1', created['id'])
    assert 'binary.dat' in preview['unsupported']
    assert 'conversations.sqlite3' in preview['unsupported']
    requests = api.restore_policy_requests('s1', created['id'])
    assert all(r['path'] not in ('binary.dat', 'conversations.sqlite3') for r in requests)
    (root / 'binary.dat').write_bytes(b'\x00\xffkeep newer')
    (root / 'readme.txt').write_text('edited', encoding='utf-8')
    preview = api.preview('s1', created['id'])
    api.restore('s1', created['id'], preview_hash=preview['preview_hash'],
                expected_revision=preview['expected_revision'])
    assert (root / 'readme.txt').read_text(encoding='utf-8') == 'Before\n'
    assert (root / 'binary.dat').read_bytes() == b'\x00\xffkeep newer'
