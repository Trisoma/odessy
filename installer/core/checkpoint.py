"""Atomic installation checkpoints and an OS-released single-writer lock."""
from __future__ import annotations
import contextlib
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from installer.core.download import _reject_link
from installer.core.manifest import _unique_keys
from installer.core.atomic import replace as replace_record

STEP_STATUSES = {'pending', 'running', 'completed', 'failed', 'cancelled'}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def state_path(root: Path) -> Path:
    return root / '.installer/state.json'


def read_state(root: Path) -> dict:
    path = state_path(root); _reject_link(root); _reject_link(path.parent); _reject_link(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Installation state is unexpectedly large.')
    data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_keys)
    fields = {'schema_version', 'plan', 'plan_sha256', 'status', 'steps', 'history', 'created_at', 'updated_at', 'summary'}
    if (not isinstance(data, dict) or set(data) != fields or type(data['schema_version']) is not int
        or data['schema_version'] != 1 or not isinstance(data['plan'], dict)
        or fingerprint(data['plan']) != data['plan_sha256'] or not isinstance(data['status'], str) or data['status'] not in STEP_STATUSES
        or not isinstance(data['steps'], list) or not data['steps'] or len(data['steps']) > 50
        or not isinstance(data['history'], list) or len(data['history']) > 128
        or not isinstance(data['summary'], dict)):
        raise ValueError('Invalid or corrupted installation state; existing files are preserved.')
    seen = set()
    for step in data['steps']:
        if (not isinstance(step, dict) or set(step) != {'name', 'status', 'attempts', 'error', 'result', 'action', 'started_at', 'ended_at'}
            or not isinstance(step['name'], str) or step['name'] in seen or not isinstance(step['status'], str) or step['status'] not in STEP_STATUSES
            or type(step['attempts']) is not int or step['attempts'] < 0):
            raise ValueError('Invalid installation step checkpoint.')
        seen.add(step['name'])
    if (set(data['summary']) != {'workflow_complete', 'installation_ready'}
        or data['summary']['installation_ready'] is not False
        or data['summary']['workflow_complete'] is not (data['status'] == 'completed')
        or (data['status'] == 'completed' and any(s['status'] != 'completed' for s in data['steps']))):
        raise ValueError('Installation completion flags disagree with the checkpoints.')
    return data


def save_state(root: Path, data: dict):
    path = state_path(root); _reject_link(path.parent); _reject_link(path)
    temporary = None
    data['updated_at'] = timestamp()
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.state-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((json.dumps(data, ensure_ascii=True, indent=2) + '\n').encode('utf-8'))
            stream.flush(); os.fsync(stream.fileno())
        replace_record(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def writer_lock(root: Path):
    """The file can remain after a crash; the kernel lock cannot."""
    path = root / '.installer/run.lock'; _reject_link(path)
    with path.open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'\0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError('Another installation workflow owns this directory; no checkpoints were changed.') from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class Step:
    name: str
    prepare: Callable[[], dict]
    validate: Callable[[], dict | None]
    always_run: bool = False


def run_steps(root: Path, plan: dict, steps: list[Step], *, resume=False, emit=lambda event: None, mutable_options=(), cancelled=lambda: False) -> dict:
    if not root.is_absolute():
        raise ValueError('Installation directory must be absolute.')
    _reject_link(root); _reject_link(root / '.installer')
    exists = state_path(root).exists()
    if resume and not exists:
        raise ValueError('No checkpoint exists to resume.')
    if not exists and root.exists() and any(root.iterdir()):
        raise ValueError('A new workflow requires an empty/new installation directory; existing files are preserved.')
    root.mkdir(parents=True, exist_ok=True); (root / '.installer').mkdir(exist_ok=True)
    with writer_lock(root):
        if state_path(root).exists():
            data = read_state(root)
            if not resume:
                raise ValueError('An installation checkpoint already exists. Use --status or explicitly --resume.')
            previous = {key: value for key, value in data['plan'].items() if key not in mutable_options}
            current = {key: value for key, value in plan.items() if key not in mutable_options}
            if previous != current or [s['name'] for s in data['steps']] != [s.name for s in steps]:
                raise ValueError('Installation paths/release/options changed; use the original plan or a new installation directory.')
            data['plan'] = plan; data['plan_sha256'] = fingerprint(plan)
        else:
            now = timestamp()
            data = dict(schema_version=1, plan=plan, plan_sha256=fingerprint(plan), status='pending',
                steps=[dict(name=s.name, status='pending', attempts=0, error=None, result=None, action=None,
                            started_at=None, ended_at=None) for s in steps], history=[], created_at=now, updated_at=now,
                summary={'workflow_complete': False, 'installation_ready': False})
        for item in data['steps']:
            item.update(status='pending', error=None, result=None, action=None, started_at=None, ended_at=None)
        data['status'] = 'running'; data['summary']['workflow_complete'] = False
        save_state(root, data)
        def notify(index, status, error=None):
            event = dict(step=steps[index].name, index=index + 1, total=len(steps), status=status,
                         completed_steps=sum(s['status'] == 'completed' for s in data['steps']), at=timestamp())
            if error is not None:
                event['error'] = error
            data['history'].append(event); data['history'] = data['history'][-128:]
            save_state(root, data); emit(dict(event))
        for index, step in enumerate(steps):
            item = data['steps'][index]
            item.update(status='running', attempts=item['attempts'] + 1, error=None, started_at=timestamp(), ended_at=None)
            notify(index, 'running')
            try:
                if cancelled():
                    raise KeyboardInterrupt()
                result = None if step.always_run else step.validate()
                if result is None:
                    item['action'] = 'prepared'
                    result = step.prepare()
                    if not step.always_run and step.validate() is None:
                        raise ValueError('Step returned without a verifiable complete output: ' + step.name)
                else:
                    item['action'] = 'reused'
                if not isinstance(result, dict):
                    raise ValueError('Stage did not return a verifiable result record: ' + step.name)
                item.update(status='completed', result=result, ended_at=timestamp())
                notify(index, 'completed')
            except BaseException as error:
                status = 'cancelled' if isinstance(error, KeyboardInterrupt) else 'failed'
                item.update(status=status, error=str(error)[:2000] or type(error).__name__, ended_at=timestamp())
                data['status'] = status
                notify(index, status, item['error'])
                raise
        data['status'] = 'completed'; data['summary']['workflow_complete'] = True
        save_state(root, data)
        return data
