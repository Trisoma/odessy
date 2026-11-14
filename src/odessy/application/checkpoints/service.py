"""Workspace-scoped file checkpoints over local Git, without touching user state.

Everything here is additive: a private temporary index, owned refs under
``refs/odessy/checkpoints/*`` and an owned JSON metadata file outside the workspace.
The user's index, staging area, branches and working tree are never reset, cleaned or
checked out.
"""
from __future__ import annotations

import asyncio
import contextvars
import concurrent.futures
import hashlib
import inspect
import json
import os
import re
import stat
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

SCHEMA_VERSION = 1
MAX_FILES = 1000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
MAX_LABEL = 120
RETENTION = 20
GIT_TIMEOUT = 60
REF_PREFIX = 'refs/odessy/checkpoints/'
_GRANT_LOOP = contextvars.ContextVar('checkpoint_grant_loop', default=None)

_ID = re.compile(r'^ckpt_[0-9a-f]{32}$')
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')


class CheckpointError(Exception):
    """Transport-neutral failure; routes translate ``status``."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Checkpoint:
    id: str
    label: str
    created_at: str
    revision: int
    workspace_id: str
    files: int
    bytes: int
    files_sha256: Mapping[str, str] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            'version': 1,
            'id': self.id,
            'label': self.label,
            'created_at': self.created_at,
            'revision': self.revision,
            'workspace_id': self.workspace_id,
            'files': self.files,
            'bytes': self.bytes,
        }


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()) + 'Z'


def _validate_label(label: Any) -> str:
    if not isinstance(label, str):
        raise CheckpointError(400, 'label must be a string')
    label = label.strip()
    if not label or len(label) > MAX_LABEL or _CONTROL.search(label):
        raise CheckpointError(400, 'label must be 1-120 printable characters')
    return label


def _validate_id(checkpoint_id: Any) -> str:
    if not isinstance(checkpoint_id, str) or not _ID.match(checkpoint_id):
        raise CheckpointError(404, 'Checkpoint not found')
    return checkpoint_id


class CheckpointService:
    """Snapshots and previewed restores for one workspace per live session.

    ``inspector`` must expose ``binding(sid) -> WorkspaceBinding`` (the existing
    ``app.state.workspace_inspector``). ``request_grant`` is the trusted broker
    callback ``(session_id, plan) -> bool``; without it nothing mutates.
    ``state_dir`` is an owned directory outside the workspace for metadata and
    temporary indexes. ``mode_revision`` optionally rechecks the access-mode revision.
    """

    def __init__(
        self,
        *,
        inspector: Any,
        request_grant: Callable[[str, Mapping[str, Any]], bool] | None = None,
        state_dir: str | os.PathLike[str],
        mode_revision: Callable[[str], int] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._inspector = inspector
        self._grant = request_grant
        self._root = Path(state_dir)
        self._mode_revision = mode_revision
        self._clock = clock or time.monotonic
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ------------------------------------------------------------------ state
    def _binding(self, sid: Any):
        if not isinstance(sid, str) or not sid:
            raise CheckpointError(404, 'Session not found')
        try:
            binding = self._inspector.binding(sid)
        except CheckpointError:
            raise
        except (KeyError, LookupError):
            raise CheckpointError(404, 'Session not found') from None
        except Exception as error:
            status = getattr(error, 'status', None) or getattr(error, 'status_code', None)
            if status in (403, 404, 409, 503):
                raise CheckpointError(int(status), str(error)) from None
            raise CheckpointError(503, 'Workspace owner unavailable') from None
        if binding is None:
            raise CheckpointError(503, 'Workspace owner unavailable')
        if getattr(binding, 'local', None) is not True or binding.is_authorized() is not True:
            raise CheckpointError(403, 'Workspace access denied')
        return binding

    def _workspace_dir(self, binding) -> Path:
        # Durable snapshots belong to the root, never an ephemeral service/session.
        root = Path(binding.root_path).resolve()
        info = root.stat()
        key = hashlib.sha256(_canonical([os.path.normcase(str(root)),
                                        info.st_dev, info.st_ino]).encode()).hexdigest()[:32]
        directory = self._root / key
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _lock(self, binding) -> threading.Lock:
        key = hashlib.sha256(_canonical(list(binding.identity())).encode()).hexdigest()
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _metadata_path(self, binding) -> Path:
        return self._workspace_dir(binding) / 'checkpoints.json'

    def _read_metadata(self, binding) -> dict[str, Any]:
        path = self._metadata_path(binding)
        if not path.exists():
            return {'schema_version': SCHEMA_VERSION, 'revision': 0, 'checkpoints': []}
        try:
            data = json.loads(path.read_text('utf-8'))
        except (OSError, ValueError):
            raise CheckpointError(503, 'Checkpoint metadata is unreadable') from None
        if (data.get('schema_version') != SCHEMA_VERSION
                or not isinstance(data.get('revision'), int)
                or not isinstance(data.get('checkpoints'), list)):
            raise CheckpointError(503, 'Checkpoint metadata is invalid')
        return data

    def _write_metadata(self, binding, data: dict[str, Any]) -> None:
        path = self._metadata_path(binding)
        payload = _canonical(data).encode('utf-8')
        if len(payload) > 2 * 1024 * 1024:
            raise CheckpointError(413, 'Checkpoint metadata exceeds its bound')
        handle, tmp = tempfile.mkstemp(dir=str(path.parent), prefix='.ckpt-', suffix='.tmp')
        try:
            with os.fdopen(handle, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ------------------------------------------------------------------ git
    def _git(self, root: Path, *args: str, env: dict[str, str] | None = None,
             input_bytes: bytes | None = None) -> bytes:
        environment = dict(os.environ)
        environment.update({'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0',
                            'LC_ALL': 'C'})
        if env:
            environment.update(env)
        try:
            result = subprocess.run(['git', '-c', 'core.fsmonitor=false',
                                    '-C', str(root), *args],
                                    input=input_bytes, capture_output=True,
                                    timeout=GIT_TIMEOUT, env=environment, check=False)
        except FileNotFoundError:
            raise CheckpointError(501, 'Git is not available on this computer') from None
        except subprocess.TimeoutExpired:
            raise CheckpointError(503, 'Git did not finish in time') from None
        if result.returncode != 0:
            raise CheckpointError(501, 'Checkpoint Git operation failed')
        return result.stdout

    def _repository_root(self, binding) -> Path:
        """Only a real, non-bare repository rooted exactly at the workspace."""
        root = Path(binding.root_path)
        try:
            top = self._git(root, 'rev-parse', '--show-toplevel').decode().strip()
            bare = self._git(root, 'rev-parse', '--is-bare-repository').decode().strip()
        except CheckpointError:
            raise CheckpointError(
                501, 'Checkpoints require a Git workspace root; this workspace is not '
                     'a Git repository') from None
        if bare == 'true' or not top:
            raise CheckpointError(501, 'Checkpoints require a non-bare Git workspace')
        if Path(top).resolve() != root.resolve():
            raise CheckpointError(
                501, 'Checkpoints require the workspace to be the repository root')
        return root

    # ------------------------------------------------------------------ capture
    def _candidate_paths(self, root: Path) -> list[str]:
        raw = self._git(root, 'ls-files', '-z', '-c', '-o', '--exclude-standard')
        paths: list[str] = []
        for item in raw.split(b'\x00'):
            if not item:
                continue
            try:
                path = item.decode('utf-8')
            except UnicodeDecodeError:
                continue
            if path.startswith('.git/') or '/.git/' in path or _CONTROL.search(path):
                continue
            paths.append(path)
        return sorted(set(paths))

    def _collect(self, binding, root: Path) -> tuple[list[str], list[str], dict[str, str], int]:
        """Returns (captured, unsupported, sha256 per path, total bytes)."""
        policy = binding.policy
        captured: list[str] = []
        unsupported: list[str] = []
        hashes: dict[str, str] = {}
        total = 0
        for path in self._candidate_paths(root):
            if len(captured) >= MAX_FILES:
                unsupported.append('*: entry limit reached')
                break
            candidate = root / path
            try:
                info = candidate.lstat()
                if candidate.is_symlink():
                    unsupported.append(path)
                    continue
                if not candidate.is_file():
                    continue
                resolved = policy.authority.resolve_path(path, must_exist=True)
                if resolved.sensitive:
                    unsupported.append(path)
                    continue
                if info.st_size > MAX_FILE_BYTES or total + info.st_size > MAX_TOTAL_BYTES:
                    unsupported.append(path)
                    continue
                data = candidate.read_bytes()
            except (OSError, ValueError, Exception):
                unsupported.append(path)
                continue
            captured.append(path)
            hashes[path] = _hash_bytes(data)
            total += len(data)
        return captured, unsupported, hashes, total

    def _snapshot(self, binding, label: str, *, kind: str) -> Checkpoint:
        root = self._repository_root(binding)
        captured, unsupported, hashes, total = self._collect(binding, root)
        if not captured and unsupported:
            raise CheckpointError(413, 'No checkpointable files fit the size limits')
        checkpoint_id = 'ckpt_' + uuid.uuid4().hex
        directory = self._workspace_dir(binding)
        handle, index_path = tempfile.mkstemp(dir=str(directory), prefix='.index-')
        os.close(handle)  # Windows keeps the file locked while the descriptor is open.
        index = Path(index_path)
        try:
            env = {'GIT_INDEX_FILE': str(index)}
            self._git(root, 'read-tree', '--empty', env=env)
            for path in captured:
                # Store raw reviewed bytes: git add may execute repository filters.
                candidate = self._safe_path(binding, path, must_exist=True)
                data = candidate.read_bytes()
                if len(data) > MAX_FILE_BYTES or _hash_bytes(data) != hashes[path]:
                    raise CheckpointError(409, 'Workspace changed during checkpoint capture')
                blob = self._git(root, 'hash-object', '-w', '--stdin',
                                 input_bytes=data).decode().strip()
                self._git(root, 'update-index', '--add', '--cacheinfo',
                          f'100644,{blob},{path}', env=env)
            tree = self._git(root, 'write-tree', env=env).decode().strip()
            if not tree:
                raise CheckpointError(503, 'Checkpoint snapshot failed')
            commit = self._git(
                root, 'commit-tree', tree, '-p', 'HEAD', '-m',
                f'odessy checkpoint {kind}: {label}'.encode('utf-8')[:512],
            ).decode().strip()
            self._git(root, 'update-ref', f'{REF_PREFIX}{checkpoint_id}', commit)
        finally:
            try:
                index.unlink(missing_ok=True)
            except OSError:
                # A lingering antivirus/indexer handle is not a checkpoint failure;
                # the file lives inside our own state directory.
                pass

        checkpoint = Checkpoint(
            id=checkpoint_id, label=label, created_at=_now(), revision=0,
            workspace_id=str(binding.workspace_id), files=len(captured),
            bytes=total, files_sha256=dict(hashes))
        with self._lock(binding):
            data = self._read_metadata(binding)
            data['revision'] = int(data['revision']) + 1
            checkpoint = Checkpoint(**{**checkpoint.__dict__,
                                       'revision': data['revision']})
            data['checkpoints'].append({'checkpoint': checkpoint.public(),
                                        'kind': kind,
                                        'files_sha256': hashes,
                                        'unsupported': unsupported[:200]})
            self._prune(binding, data)
            self._write_metadata(binding, data)
        return checkpoint

    def _prune(self, binding, data: dict[str, Any]) -> None:
        entries = data['checkpoints']
        if len(entries) <= RETENTION:
            return
        keep, drop = entries[-RETENTION:], entries[:-RETENTION]
        for entry in drop:
            checkpoint_id = entry['checkpoint']['id']
            try:
                self._git(Path(binding.root_path), 'update-ref', '-d',
                          f'{REF_PREFIX}{checkpoint_id}')
            except CheckpointError:
                # A missing ref is already gone; metadata still drops it.
                pass
        data['checkpoints'] = keep

    # ------------------------------------------------------------------ read
    def _entries(self, binding) -> dict[str, dict[str, Any]]:
        data = self._read_metadata(binding)
        return {entry['checkpoint']['id']: entry for entry in data['checkpoints']}

    def list(self, sid: str) -> dict[str, Any]:
        binding = self._binding(sid)
        data = self._read_metadata(binding)
        return {'version': 1,
                'checkpoints': [{**entry['checkpoint'], 'workspace_id': binding.workspace_id}
                                for entry in data['checkpoints']]}

    def _entry(self, binding, checkpoint_id: str) -> dict[str, Any]:
        entry = self._entries(binding).get(_validate_id(checkpoint_id))
        if entry is None:
            raise CheckpointError(404, 'Checkpoint not found')
        return entry

    def _current_hashes(self, binding, entry) -> dict[str, str | None]:
        root = Path(binding.root_path)
        current: dict[str, str | None] = {}
        for path in entry['files_sha256']:
            candidate = self._safe_path(binding, path)
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    current[path] = None
                    continue
                if candidate.stat().st_size > MAX_FILE_BYTES:
                    raise CheckpointError(413, 'Current file exceeds checkpoint preview limit')
                current[path] = _hash_bytes(candidate.read_bytes())
            except OSError:
                current[path] = None
        return current

    def _safe_path(self, binding, path, *, must_exist=False):
        context = _GRANT_LOOP.get()
        if context is not None and context[1].is_set():
            raise CheckpointError(403, 'Checkpoint request cancelled')
        root = Path(binding.root_path).resolve()
        info = root.stat()
        if (info.st_dev, info.st_ino) != binding.root_identity or not binding.is_authorized():
            raise CheckpointError(403, 'Workspace authority is no longer valid')
        candidate = root / path
        try:
            current = root
            for part in Path(path).parts:
                current = current / part
                try:
                    info = current.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise ValueError('Linked path')
            resolved = binding.policy.authority.resolve_path(path, must_exist=must_exist)
            if resolved.sensitive or candidate.is_symlink():
                raise ValueError('Denied')
            actual = candidate.resolve()
            if root not in (actual, *actual.parents):
                raise ValueError('Escape')
        except (OSError, ValueError, PermissionError):
            raise CheckpointError(403, 'Checkpoint workspace path denied') from None
        return candidate

    def _plan(self, binding, entry) -> dict[str, Any]:
        expected = entry['files_sha256']
        current = self._current_hashes(binding, entry)
        changes = []
        for path, recorded in expected.items():
            now = current.get(path)
            if now is None:
                status = 'missing'
            elif now == recorded:
                status = 'unchanged'
            else:
                status = 'modified'
            changes.append({'path': path, 'status': status})
        changes.sort(key=lambda item: item['path'])
        unsupported = list(entry.get('unsupported', []))
        # The hash binds this exact working-tree content, not just the status list:
        # a second edit with the same 'modified' status must still invalidate it.
        payload = {'checkpoint_id': entry['checkpoint']['id'],
                   'revision': entry['checkpoint']['revision'],
                   'changes': changes, 'unsupported': unsupported,
                   'current': current}
        return {'version': 1,
                'checkpoint_id': entry['checkpoint']['id'],
                'expected_revision': entry['checkpoint']['revision'],
                'preview_hash': _hash_bytes(_canonical(payload).encode()),
                'changes': changes,
                'unsupported': unsupported}

    def preview(self, sid: str, checkpoint_id: str) -> dict[str, Any]:
        binding = self._binding(sid)
        return self._plan(binding, self._entry(binding, checkpoint_id))

    # ------------------------------------------------------------------ mutate
    def create(self, sid: str, label: Any, *, kind: str = 'manual') -> dict[str, Any]:
        binding = self._binding(sid)
        label = _validate_label(label)
        self._authorize(sid, 'checkpoint.create', {'label': label, 'kind': kind})
        return self._snapshot(binding, label, kind=kind).public()

    def _authorize(self, sid: str, action: str, details: Mapping[str, Any]) -> None:
        if self._grant is None:
            raise CheckpointError(
                501, 'Checkpoint authorization is not wired; no snapshot was taken')
        plan = {'action': action, 'session_id': sid,
                'scopes': ['authorization'], **details}
        binding = self._binding(sid)
        identity = binding.identity()
        revision = self._mode_revision(sid) if self._mode_revision else None
        try:
            approved = self._grant(sid, plan)
            if inspect.isawaitable(approved):
                context = _GRANT_LOOP.get()
                if context is None:
                    if inspect.iscoroutine(approved):
                        approved.close()
                    raise CheckpointError(501, 'Async authorization requires the async checkpoint route')
                async def wait_for_grant():
                    return await approved
                loop, cancelled = context
                future = asyncio.run_coroutine_threadsafe(wait_for_grant(), loop)
                deadline = self._clock() + 300
                try:
                    while True:
                        if cancelled.is_set() or self._clock() >= deadline:
                            raise CheckpointError(403, 'Checkpoint approval cancelled or expired')
                        try:
                            approved = future.result(timeout=0.1)
                            break
                        except concurrent.futures.TimeoutError:
                            continue
                finally:
                    if not future.done():
                        future.cancel()
        except CheckpointError:
            raise
        except Exception:
            raise CheckpointError(403, 'Checkpoint authorization failed') from None
        if approved is not True:
            raise CheckpointError(403, 'Checkpoint authorization was refused')
        if self._binding(sid).identity() != identity:
            raise CheckpointError(409, 'Workspace authority changed during authorization')
        if self._mode_revision and self._mode_revision(sid) != revision:
            raise CheckpointError(409, 'Access mode changed during authorization')
        context = _GRANT_LOOP.get()
        if context is not None and context[1].is_set():
            raise CheckpointError(403, 'Checkpoint request cancelled')

    async def run_async(self, function, *args, **kwargs):
        """Keep Git off the API loop; run async approvals on that owning loop."""
        cancelled = threading.Event()
        token = _GRANT_LOOP.set((asyncio.get_running_loop(), cancelled))
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        finally:
            cancelled.set()
            _GRANT_LOOP.reset(token)

    def restore(self, sid: str, checkpoint_id: str, *, preview_hash: str,
                expected_revision: int) -> dict[str, Any]:
        binding = self._binding(sid)
        if not isinstance(preview_hash, str) or len(preview_hash) != 64:
            raise CheckpointError(400, 'preview_hash is required')
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise CheckpointError(400, 'expected_revision must be an integer')
        entry = self._entry(binding, checkpoint_id)
        plan = self._plan(binding, entry)
        if entry['checkpoint']['revision'] != expected_revision:
            raise CheckpointError(409, 'Checkpoint revision changed; preview again')
        if plan['preview_hash'] != preview_hash:
            raise CheckpointError(409, 'Workspace changed since the preview; review again')
        self._authorize(sid, 'checkpoint.restore', {
            'checkpoint_id': entry['checkpoint']['id'],
            'preview_hash': preview_hash,
            'changes': [c for c in plan['changes'] if c['status'] != 'unchanged'],
        })
        # Revalidate immediately before the effect (grant prompts can take time).
        binding = self._binding(sid)
        if self._plan(binding, self._entry(binding, checkpoint_id))['preview_hash'] != preview_hash:
            raise CheckpointError(409, 'Workspace changed during authorization')
        safety = self._snapshot(binding, f'pre-restore {entry["checkpoint"]["label"]}'[:MAX_LABEL],
                                kind='safety')
        self._apply(binding, entry)
        with self._lock(binding):
            data = self._read_metadata(binding)
            data['revision'] = int(data['revision']) + 1
            self._write_metadata(binding, data)
        return {'version': 1, 'checkpoint_id': entry['checkpoint']['id'],
                'safety_checkpoint_id': safety.id, 'status': 'restored'}

    def _apply(self, binding, entry) -> None:
        root = Path(binding.root_path)
        ref = f'{REF_PREFIX}{entry["checkpoint"]["id"]}'
        restored = 0
        for path in sorted(entry['files_sha256']):
            target = self._safe_path(binding, path)
            parent = target.parent
            try:
                if parent.is_symlink():
                    raise CheckpointError(403, 'Workspace path denied during restore')
                parent.mkdir(parents=True, exist_ok=True)
                resolved_parent = parent.resolve()
                if root.resolve() not in (resolved_parent, *resolved_parent.parents):
                    raise CheckpointError(403, 'Workspace path escaped during restore')
                data = self._git(root, 'cat-file', 'blob', f'{ref}:{path}')
                if _hash_bytes(data) != entry['files_sha256'][path]:
                    raise CheckpointError(409, 'Checkpoint content changed; restore refused')
            except CheckpointError:
                raise
            except OSError:
                raise CheckpointError(503, 'Checkpoint file could not be restored') from None
            handle, tmp = tempfile.mkstemp(dir=str(parent), prefix='.ckpt-', suffix='.tmp')
            try:
                with os.fdopen(handle, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, target)
                restored += 1
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

    def delete(self, sid: str, checkpoint_id: str) -> None:
        binding = self._binding(sid)
        entry = self._entry(binding, checkpoint_id)
        self._authorize(sid, 'checkpoint.delete', {'checkpoint_id': checkpoint_id})
        with self._lock(binding):
            data = self._read_metadata(binding)
            data['checkpoints'] = [item for item in data['checkpoints']
                                   if item['checkpoint']['id'] != entry['checkpoint']['id']]
            data['revision'] = int(data['revision']) + 1
            self._write_metadata(binding, data)
        try:
            self._git(Path(binding.root_path), 'update-ref', '-d',
                      f'{REF_PREFIX}{checkpoint_id}')
        except CheckpointError:
            pass

    def close_session(self, sid: str) -> None:
        """Keep shared locks stable while another authorized session may use them."""
        return None
