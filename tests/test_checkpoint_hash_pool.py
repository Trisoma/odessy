"""File-level content addressing for checkpoint metadata (CAS hash pool)."""
from __future__ import annotations

import json
import subprocess

import pytest

from odessy.application.checkpoints.service import CheckpointError, CheckpointService
from test_checkpoint_service import Binding, Grants, Inspector, git


def needs_git() -> bool:
    try:
        subprocess.run(['git', '--version'], capture_output=True, timeout=20, check=True)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not needs_git(), reason='git is not installed')


def make_ws(tmp_path, files=None):
    root = tmp_path / 'workspace'
    root.mkdir()
    files = files or {'a.txt': 'A\n', 'b.txt': 'B\n', 'c.txt': 'C\n'}
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    subprocess.run(['git', 'init', '-q', str(root)], check=True, timeout=60)
    git(root, 'config', 'user.email', 'fixture@example.test')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', 'fixture base')
    return root, Binding(root)


def make_api(tmp_path, binding):
    return CheckpointService(inspector=Inspector(binding), request_grant=Grants(),
                             state_dir=tmp_path / 'state')


def load_meta(api, binding):
    return json.loads(api._metadata_path(binding).read_text('utf-8'))


def test_hash_pool_reuses_unchanged_files(tmp_path):
    """Editing one file adds exactly one pool item, not a whole new hash map."""
    root, binding = make_ws(tmp_path)
    api = make_api(tmp_path, binding)
    first = api.auto_snapshot(binding.session_id, 'first')
    meta = load_meta(api, binding)
    assert len(meta['hash_pool']) == 3
    assert all('hash_refs' in e and 'files_sha256' not in e for e in meta['checkpoints'])

    (root / 'b.txt').write_text('B2\n', encoding='utf-8')
    second = api.auto_snapshot(binding.session_id, 'second')
    assert second['id'] != first['id']
    meta = load_meta(api, binding)
    # 3 original + 1 changed = 4 distinct (path, hash) pairs, not 6.
    assert len(meta['hash_pool']) == 4


def test_hash_pool_references_resolve_to_original_hashes(tmp_path):
    """_entries presents hash_refs as the same files_sha256 dict callers expect."""
    root, binding = make_ws(tmp_path)
    api = make_api(tmp_path, binding)
    saved = api.auto_snapshot(binding.session_id, 'baseline')
    raw = load_meta(api, binding)
    entry = raw['checkpoints'][0]
    refs = entry['hash_refs']
    pool = raw['hash_pool']
    expected = {path: pool[idx].split('\x00', 1)[1] for path, idx in refs.items()}
    resolved = api._entries(binding)[saved['id']]['files_sha256']
    assert resolved == expected
    assert set(resolved) == {'a.txt', 'b.txt', 'c.txt'}

    # Preview/restore still work through the resolved hashes.
    (root / 'a.txt').write_text('changed\n', encoding='utf-8')
    preview = api.preview(binding.session_id, saved['id'])
    assert any(c['path'] == 'a.txt' and c['status'] == 'modified' for c in preview['changes'])


def test_gc_reclaims_unreferenced_pool_items_and_remaps(tmp_path):
    """Deleting a snapshot frees its exclusive pool items without dangling refs."""
    root, binding = make_ws(tmp_path)
    api = make_api(tmp_path, binding)
    first = api.auto_snapshot(binding.session_id, 'first')
    (root / 'b.txt').write_text('B2\n', encoding='utf-8')
    second = api.auto_snapshot(binding.session_id, 'second')

    before = load_meta(api, binding)
    assert len(before['hash_pool']) == 4
    changed_item = None
    for item in before['hash_pool']:
        if item.startswith('b.txt\x00') and item.endswith(pool_hash(before, second['id'], 'b.txt')):
            changed_item = item
    assert changed_item is not None

    # Delete the newer snapshot that owns the changed b.txt hash.
    api.delete(binding.session_id, second['id'])
    after = load_meta(api, binding)
    assert changed_item not in after['hash_pool']
    assert len(after['hash_pool']) == 3
    # Remaining snapshot's references are still valid and resolve correctly.
    remaining = api._entries(binding)[first['id']]['files_sha256']
    # b.txt was never edited; its hash must equal what the FIRST snapshot recorded.
    first_item = before['hash_pool'][before['checkpoints'][0]['hash_refs']['b.txt']]
    assert remaining['b.txt'] == first_item.split('\x00', 1)[1]


def pool_hash(meta, checkpoint_id, path):
    entry = next(e for e in meta['checkpoints'] if e['checkpoint']['id'] == checkpoint_id)
    return meta['hash_pool'][entry['hash_refs'][path]].split('\x00', 1)[1]


def test_v1_inline_metadata_stays_readable(tmp_path):
    """Legacy entries with inline files_sha256 are untouched and still usable."""
    root, binding = make_ws(tmp_path)
    api = make_api(tmp_path, binding)
    saved = api.auto_snapshot(binding.session_id, 'baseline')
    meta_path = api._metadata_path(binding)

    # Rewrite metadata into the pre-CAS shape: inline hashes, no pool.
    meta = load_meta(api, binding)
    for entry in meta['checkpoints']:
        refs = entry.pop('hash_refs')
        entry['files_sha256'] = {p: meta['hash_pool'][i].split('\x00', 1)[1]
                                 for p, i in refs.items()}
    meta.pop('hash_pool', None)
    meta_path.write_text(json.dumps(meta), encoding='utf-8')

    entries = api._entries(binding)
    assert saved['id'] in entries
    assert entries[saved['id']]['files_sha256']['a.txt']  # non-empty sha256
    (root / 'a.txt').write_text('different\n', encoding='utf-8')
    preview = api.preview(binding.session_id, saved['id'])
    assert any(c['path'] == 'a.txt' and c['status'] == 'modified' for c in preview['changes'])


def test_corrupt_pool_reference_is_refused(tmp_path):
    """A dangling hash_ref is rejected rather than silently mis-resolved."""
    root, binding = make_ws(tmp_path)
    api = make_api(tmp_path, binding)
    api.auto_snapshot(binding.session_id, 'baseline')
    meta = load_meta(api, binding)
    meta['checkpoints'][0]['hash_refs']['a.txt'] = 999
    api._metadata_path(binding).write_text(json.dumps(meta), encoding='utf-8')
    with pytest.raises(CheckpointError) as exc:
        api._entries(binding)
    assert exc.value.status == 503
