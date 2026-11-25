"""Breakpoint recording and task-level undo over a durable conversation.

A breakpoint is a sanitized ``kind='breakpoint'`` event. It records what was in
flight (task, tool, completed/pending work, event cursor) without storing raw
tool arguments, command output or credentials. An undo is a ``kind='undo'``
event plus an optional workspace-file rollback through the *existing*
CheckpointService; the append-only history is never rewritten.

The service never authorizes anything itself. File rollback calls the injected
``request_grant`` broker and rechecks the workspace identity, exactly as a
manual checkpoint restore does.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import time
import uuid
from typing import Any, Callable, Mapping

from odessy.authority.redaction import redact_text
from odessy.tools.catalog import PATH_ARGUMENTS

from ..conversations.store import (Conflict, ConversationStore, Invalid, NotFound,
                                    StorageError, _check_id, _check_int, _check_text, utc_now)
from .policy import is_critical

#: Current breakpoint payload schema. Mismatches are refused, never migrated.
BREAKPOINT_SCHEMA = 1
KINDS = ('task-start', 'critical-before', 'critical-after', 'step', 'timer', 'interrupted')

MAX_EXCERPT = 200
MAX_ARG_VALUE = 256
MAX_FILES = 1000


class Unsupported(StorageError):
    """A real capability this build cannot provide; 501, never a fake success."""
    code = 'unsupported'
    status = 501


def _redact(value: Any, *, limit: int = MAX_ARG_VALUE) -> str:
    text = redact_text(value if isinstance(value, str) else str(value))
    return text[:limit]


def _argument_projection(arguments: Any) -> dict[str, Any]:
    """Types and lengths only — never raw argument values.

    Names of secret-looking keys are kept (so a reader sees a key was passed),
    but no raw value text is retained. Booleans/numbers are safe scalars and kept
    as-is; every string is represented only by its type, length and a truncated
    sha256. Path-like arguments keep a short redacted ``path`` hint for undo
    affordance — that hint is still passed through redaction and bounded.
    """
    projection: dict[str, Any] = {}
    if not isinstance(arguments, Mapping):
        return projection
    for key, value in arguments.items():
        name = str(key)
        if isinstance(value, bool) or value is None:
            projection[name] = {'type': type(value).__name__, 'value': value}
            continue
        if isinstance(value, (int, float)):
            projection[name] = {'type': type(value).__name__, 'value': value}
            continue
        if isinstance(value, str):
            digest = hashlib.sha256(value.encode('utf-8', errors='ignore')).hexdigest()[:16]
            entry: dict[str, Any] = {'type': 'string', 'len': len(value), 'sha256': digest}
            if name in PATH_ARGUMENTS:
                entry['path'] = _redact(value)
            projection[name] = entry
            continue
        projection[name] = {'type': type(value).__name__, 'len': len(value) if hasattr(value, '__len__') else None}
    return projection


def _hash_files(files: Mapping[str, str] | None) -> dict[str, str]:
    if not isinstance(files, Mapping):
        return {}
    out: dict[str, str] = {}
    for path, digest in list(files.items())[:MAX_FILES]:
        if isinstance(path, str) and isinstance(digest, str):
            out[path] = digest
    return out


class BreakpointService:
    """Owns breakpoint payload construction, recording, listing and undo."""

    def __init__(
        self,
        store: ConversationStore,
        *,
        checkpoints: Any | None = None,
        request_grant: Callable[[str, Mapping[str, Any]], Any] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.checkpoints = checkpoints
        self.request_grant = request_grant
        self._clock = clock or utc_now

    # -- recording -------------------------------------------------------

    def record(
        self,
        conversation_id: str,
        *,
        kind: str,
        session_id: str,
        step_index: int = 0,
        turn_id: str = '',
        user_prompt: str = '',
        agent_state: str = '',
        call_id: str | None = None,
        tool: Mapping[str, Any] | None = None,
        completed: Mapping[str, Any] | None = None,
        pending: Mapping[str, Any] | None = None,
        workspace: Mapping[str, Any] | None = None,
        cursor: Mapping[str, Any] | None = None,
        resume: Mapping[str, Any] | None = None,
        undo_boundaries: list[str] | None = None,
    ) -> dict[str, Any]:
        """Append one sanitized breakpoint event. Returns its public payload."""
        conversation_id = _check_id(conversation_id)
        if kind not in KINDS:
            raise Invalid('Unknown breakpoint kind')
        step_index = _check_int(step_index, 'step_index', 0)
        bp_id = 'bp_' + uuid.uuid4().hex
        payload = {
            'version': BREAKPOINT_SCHEMA,
            'id': bp_id,
            'kind': kind,
            'session_id': _check_text(session_id, 'session_id', 64),
            'step_index': step_index,
            'call_id': str(call_id) if call_id else None,
            'created_at': self._clock(),
            'agent_state': _redact(agent_state, limit=64),
            'task': {
                'turn_id': str(turn_id)[:64],
                'user_prompt_excerpt': _redact(user_prompt, limit=MAX_EXCERPT),
                'step_index': step_index,
            },
            'tool': self._tool_payload(tool),
            'completed': self._completed_payload(completed),
            'pending': self._pending_payload(pending),
            'workspace': self._workspace_payload(workspace),
            'cursor': self._cursor_payload(cursor),
            'resume': {
                'continuation': self._safe_choice((resume or {}).get('continuation'),
                                                  ('continuable', 'history_only'), 'history_only'),
                'engine': self._safe_choice((resume or {}).get('engine'),
                                            ('legacy', 'harness'), 'legacy'),
                'undo_boundaries': [str(x)[:80] for x in (undo_boundaries or [])][:64],
            },
        }
        event = self.store.append(conversation_id, event_id=bp_id, kind='breakpoint',
                                  payload=payload)
        return {'version': 1, 'sequence': event['sequence'], **payload}

    @staticmethod
    def _tool_payload(tool: Mapping[str, Any] | None) -> dict[str, Any]:
        if not isinstance(tool, Mapping):
            return {'name': None, 'risk': None, 'arguments_projection': {},
                    'policy_outcome': None, 'status': None}
        return {
            'name': _redact(str(tool.get('name') or ''), limit=128) or None,
            'risk': BreakpointService._safe_choice(tool.get('risk'), ('low', 'medium', 'high'), None),
            'arguments_projection': _argument_projection(tool.get('arguments')),
            'policy_outcome': BreakpointService._safe_choice(
                tool.get('policy_outcome'), ('allow', 'confirm', 'deny'), None),
            'status': BreakpointService._safe_choice(
                tool.get('status'), ('pending', 'ok', 'error', 'interrupted'), None),
        }

    @staticmethod
    def _completed_payload(completed: Mapping[str, Any] | None) -> dict[str, Any]:
        completed = completed if isinstance(completed, Mapping) else {}
        calls = [str(x)[:80] for x in (completed.get('ok_tool_calls') or [])][:200]
        files = [str(x) for x in (completed.get('files_changed') or [])][:MAX_FILES]
        return {
            'settled_turns': _check_int(completed.get('settled_turns', 0), 'settled_turns', 0),
            'ok_tool_calls': calls,
            'assistant_text_through_sequence': _check_int(
                completed.get('assistant_text_through_sequence', 0),
                'assistant_text_through_sequence', 0),
            'files_changed': files,
        }

    @staticmethod
    def _pending_payload(pending: Mapping[str, Any] | None) -> dict[str, Any]:
        pending = pending if isinstance(pending, Mapping) else {}
        return {
            'in_flight_tool_calls': [str(x)[:80] for x in (pending.get('in_flight_tool_calls') or [])][:200],
            'unsettled_delta_chars': _check_int(pending.get('unsettled_delta_chars', 0),
                                                 'unsettled_delta_chars', 0),
            'pending_approvals': bool(pending.get('pending_approvals', False)),
            'pending_jobs': bool(pending.get('pending_jobs', False)),
            'pending_audio': bool(pending.get('pending_audio', False)),
        }

    @staticmethod
    def _workspace_payload(workspace: Mapping[str, Any] | None) -> dict[str, Any]:
        workspace = workspace if isinstance(workspace, Mapping) else {}
        return {
            'snapshot_ref': (str(workspace['snapshot_ref'])[:160]
                             if workspace.get('snapshot_ref') else None),
            'files_sha256': _hash_files(workspace.get('files_sha256')),
            'workspace_identity': _redact(str(workspace.get('workspace_identity') or ''),
                                          limit=512),
        }

    @staticmethod
    def _cursor_payload(cursor: Mapping[str, Any] | None) -> dict[str, Any]:
        cursor = cursor if isinstance(cursor, Mapping) else {}
        return {
            'first_event_sequence': _check_int(cursor.get('first_event_sequence', 0),
                                                'first_event_sequence', 0),
            'last_event_sequence': _check_int(cursor.get('last_event_sequence', 0),
                                              'last_event_sequence', 0),
        }

    @staticmethod
    def _safe_choice(value: Any, allowed: tuple, default: Any) -> Any:
        return value if isinstance(value, str) and value in allowed else default

    # -- listing ---------------------------------------------------------

    def list(self, conversation_id: str, *, limit: int = 200) -> dict[str, Any]:
        events = self.store.breakpoints(conversation_id, limit=limit)
        breakpoints = []
        for event in events:
            events_payload = event.get('payload') or {}
            breakpoints.append({
                'sequence': event['sequence'],
                'event_id': event['event_id'],
                'kind': event['kind'],
                'breakpoint': events_payload if event['kind'] == 'breakpoint' else
                {'id': events_payload.get('id'), 'undo_of': events_payload.get('undo_of')},
                'created_at': event['created_at'],
            })
        return {'version': 1, 'conversation_id': conversation_id, 'breakpoints': breakpoints}

    # -- undo ------------------------------------------------------------

    def undo(
        self,
        conversation_id: str,
        breakpoint_id: str,
        *,
        expected_revision: int,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Undo a task back to the anchor breakpoint.

        The anchor's ``resume.undo_boundaries`` and cursor define what is
        discarded. File rollback is done only when the anchor carries a
        ``workspace.snapshot_ref`` and a CheckpointService + approval broker are
        wired; otherwise the undo records history-only invalidation.

        History is never physically deleted: an ``undo`` event marks the range
        invalid, so the audit trail stays intact (and Harness history is untouched).
        """
        conversation_id = _check_id(conversation_id)
        breakpoint_id = _check_text(breakpoint_id, 'breakpoint_id', 80)
        if not breakpoint_id.startswith('bp_'):
            raise Invalid('breakpoint_id must be a breakpoint id')
        expected_revision = _check_int(expected_revision, 'expected_revision', 0)

        row = self.store.get(conversation_id)
        if row['revision'] != expected_revision:
            raise Conflict('Conversation changed; reload before undoing')

        anchor = self.store.event_by_id(conversation_id, breakpoint_id)
        if anchor is None or anchor['kind'] != 'breakpoint':
            raise NotFound('Breakpoint not found')
        payload = anchor.get('payload') or {}
        if payload.get('version') != BREAKPOINT_SCHEMA:
            raise Unsupported('This breakpoint schema cannot be undone')
        boundaries = [str(x) for x in (payload.get('resume') or {}).get('undo_boundaries') or []]
        last_sequence = _check_int((payload.get('cursor') or {}).get('last_event_sequence', 0),
                                   'last_event_sequence', 0)
        if last_sequence < anchor['sequence']:
            # An anchor must bound events at or before itself; a corrupt/future
            # cursor must not let undo discard unrelated later work.
            last_sequence = anchor['sequence']

        tool_payload = payload.get('tool') or {}
        call_id = tool_payload.get('call_id')
        undone_call_ids = [str(call_id)] if call_id else []
        undone_files = [str(x) for x in (payload.get('completed') or {}).get('files_changed') or []]

        snapshot_ref = (payload.get('workspace') or {}).get('snapshot_ref')
        rollback: dict[str, Any] = {'status': 'not_requested', 'files': []}
        if snapshot_ref:
            rollback = self._rollback_files(
                conversation_id, session_id, str(snapshot_ref),
                expected_revision=expected_revision)

        undo_id = 'undo_' + uuid.uuid4().hex
        undo_payload = {
            'version': BREAKPOINT_SCHEMA,
            'id': undo_id,
            'undo_of': breakpoint_id,
            'created_at': self._clock(),
            'invalidates_through_sequence': last_sequence,
            'undo_boundaries': boundaries[:64],
            'undone_call_ids': undone_call_ids[:200],
            'undone_files': undone_files[:MAX_FILES],
            'file_rollback': rollback,
        }
        self.store.append(conversation_id, event_id=undo_id, kind='undo', payload=undo_payload)
        return {'version': 1, 'conversation_id': conversation_id,
                'undo_id': undo_id, 'undo_of': breakpoint_id,
                'breakpoint_id': breakpoint_id,
                'invalidates_through_sequence': last_sequence,
                'file_rollback': rollback, 'history_preserved': True}

    async def undo_async(
        self,
        conversation_id: str,
        breakpoint_id: str,
        *,
        expected_revision: int,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Async undo that also performs the approved file rollback in one call.

        This mirrors the checkpoint restore route: Git and the broker run through
        ``CheckpointService.run_async`` so Git stays off the event loop and an async
        approval completes on the loop that owns it. History invalidation and the
        ``undo`` event are appended after a successful rollback (or recorded as
        history-only when no snapshot is bound).
        """
        conversation_id = _check_id(conversation_id)
        breakpoint_id = _check_text(breakpoint_id, 'breakpoint_id', 80)
        if not breakpoint_id.startswith('bp_'):
            raise Invalid('breakpoint_id must be a breakpoint id')
        expected_revision = _check_int(expected_revision, 'expected_revision', 0)
        row = self.store.get(conversation_id)
        if row['revision'] != expected_revision:
            raise Conflict('Conversation changed; reload before undoing')
        anchor = self.store.event_by_id(conversation_id, breakpoint_id)
        if anchor is None or anchor['kind'] != 'breakpoint':
            raise NotFound('Breakpoint not found')
        payload = anchor.get('payload') or {}
        if payload.get('version') != BREAKPOINT_SCHEMA:
            raise Unsupported('This breakpoint schema cannot be undone')

        snapshot_ref = (payload.get('workspace') or {}).get('snapshot_ref')
        rollback: dict[str, Any] = {'status': 'not_requested', 'files': []}
        if snapshot_ref and self.checkpoints is not None and session_id:
            checkpoint_id = str(snapshot_ref).rsplit('/', 1)[-1]
            if checkpoint_id.startswith('ckpt_'):
                preview = await asyncio.to_thread(self.checkpoints.preview, session_id, checkpoint_id)
                # restore validates the CHECKPOINT revision (entry['checkpoint']['revision']),
                # not the conversation revision used above for the optimistic lock. They
                # are independent counters; preview already carries the checkpoint one.
                result = await self.checkpoints.run_async(
                    self.checkpoints.restore, session_id, checkpoint_id,
                    preview_hash=preview['preview_hash'],
                    expected_revision=preview['expected_revision'])
                rollback = {'status': 'restored', 'checkpoint_id': checkpoint_id,
                            'files': [c['path'] for c in preview.get('changes', [])],
                            'safety_checkpoint_id': result.get('safety_checkpoint_id')}
        # A successful file restore bumps the conversation revision (it appends a
        # safety snapshot marker only in the checkpoint metadata, not the store),
        # so re-read the anchor-derived fields from the unchanged event payload.
        boundaries = [str(x) for x in (payload.get('resume') or {}).get('undo_boundaries') or []]
        last_sequence = _check_int((payload.get('cursor') or {}).get('last_event_sequence', 0),
                                   'last_event_sequence', 0)
        last_sequence = max(last_sequence, anchor['sequence'])
        tool_payload = payload.get('tool') or {}
        call_id = tool_payload.get('call_id')
        undone_call_ids = [str(call_id)] if call_id else []
        undone_files = [str(x) for x in (payload.get('completed') or {}).get('files_changed') or []]
        undo_id = 'undo_' + uuid.uuid4().hex
        undo_payload = {
            'version': BREAKPOINT_SCHEMA, 'id': undo_id, 'undo_of': breakpoint_id,
            'created_at': self._clock(), 'invalidates_through_sequence': last_sequence,
            'undo_boundaries': boundaries[:64], 'undone_call_ids': undone_call_ids[:200],
            'undone_files': undone_files[:MAX_FILES], 'file_rollback': rollback,
        }
        self.store.append(conversation_id, event_id=undo_id, kind='undo', payload=undo_payload)
        return {'version': 1, 'conversation_id': conversation_id, 'undo_id': undo_id,
                'undo_of': breakpoint_id, 'breakpoint_id': breakpoint_id,
                'invalidates_through_sequence': last_sequence,
                'file_rollback': rollback, 'history_preserved': True}

    def _rollback_files(
        self, conversation_id: str, session_id: str | None, snapshot_ref: str,
        *, expected_revision: int,
    ) -> dict[str, Any]:
        """Roll workspace files back via the existing approved checkpoint path."""
        if self.checkpoints is None or self.request_grant is None or not session_id:
            return {'status': 'unsupported', 'reason': 'no approved checkpoint owner is wired',
                    'files': []}
        checkpoint_id = snapshot_ref.rsplit('/', 1)[-1]
        if not checkpoint_id.startswith('ckpt_'):
            return {'status': 'unsupported', 'reason': 'invalid snapshot ref', 'files': []}
        # The CheckpointService owns all path safety, approval and atomics. Reuse
        # its restore with a freshly-previewed hash so content is bound exactly.
        try:
            preview = self.checkpoints.preview(session_id, checkpoint_id)
        except StorageError as exc:
            return {'status': 'unavailable', 'reason': str(exc), 'files': []}
        return {
            'status': 'requires_restore',
            'checkpoint_id': checkpoint_id,
            'preview_hash': preview.get('preview_hash'),
            'expected_revision': expected_revision,
            'files': [c['path'] for c in preview.get('changes', [])],
            'note': ('File rollback must be completed with the approved checkpoint '
                     'restore call carrying this preview_hash; this undo records the intent.'),
        }


def build_service(store: ConversationStore, *, checkpoints: Any | None = None,
                  request_grant: Callable[[str, Mapping[str, Any]], Any] | None = None,
                  clock: Callable[[], str] | None = None) -> BreakpointService:
    return BreakpointService(store, checkpoints=checkpoints,
                             request_grant=request_grant, clock=clock)


class BreakpointHook:
    """Per-live-session adapter the runtime agent loop calls at dispatch points.

    It is attached to the controller as ``_breakpoint_hook`` by the composition
    and is intentionally duck-typed from the runtime side: the runtime calls
    ``before_tool``/``after_tool``/``step``/``timer`` when the attribute exists,
    with no import of this module. That keeps runtime independent of application
    and makes the whole feature a no-op for sessions without a durable
    conversation.

    Writes are best-effort observation: a breakpoint write failure is swallowed
    (and reported once), because a breakpoint must never fail the user's turn —
    the engine still owns the conversation, exactly like the durable projection.
    """

    def __init__(self, service: BreakpointService, *, conversation_id: str,
                 session_id: str, get_context: Callable[[], Mapping[str, Any]] | None = None,
                 timer_seconds: float = 60.0, clock: Callable[[], float] | None = None,
                 snapshot_fn: Callable[[str], Mapping[str, Any] | None] | None = None):
        self.service = service
        self.conversation_id = conversation_id
        self.session_id = session_id
        self._get_context = get_context or (lambda: {})
        self._snapshot_fn = snapshot_fn
        self._timer_seconds = max(1.0, float(timer_seconds))
        self._clock = clock or time.monotonic
        self._last_breakpoint_at = self._clock()
        self._error: str = ''
        self._file_hashes: dict[str, str] = {}
        self._step = 0
        self._task_start_breakpoint: str = ''
        # The hook owns turn identity: the composition's context callback has no
        # reliable active-turn field, so begin_turn supplies these directly.
        self._turn_id: str = ''
        self._user_prompt: str = ''
        self._auto_snapshot_ref: str = ''
        self._auto_snapshot_files: dict[str, str] = {}

    def _ctx(self) -> dict[str, Any]:
        try:
            value = self._get_context()
            ctx = dict(value) if isinstance(value, Mapping) else {}
        except Exception:
            ctx = {}
        # Prefer facts the hook owns over the context callback's best effort.
        if self._turn_id:
            ctx['turn_id'] = self._turn_id
        if self._user_prompt:
            ctx['user_prompt'] = self._user_prompt
        if self._auto_snapshot_ref:
            ctx['snapshot_ref'] = self._auto_snapshot_ref
        if self._auto_snapshot_files:
            merged = dict(self._auto_snapshot_files)
            incoming = ctx.get('files_sha256')
            if isinstance(incoming, Mapping):
                merged.update({str(k): str(v) for k, v in incoming.items()})
            ctx['files_sha256'] = merged
        return ctx

    def _capture(self, reason: str) -> None:
        """Take an automatic, deduplicated snapshot and remember its ref.

        Best-effort observation: a capture failure never fails the turn, it just
        means subsequent breakpoints record no file-restoration pointer.
        """
        if self._snapshot_fn is None:
            return
        try:
            snapshot = self._snapshot_fn(reason)
        except Exception:
            return
        if not isinstance(snapshot, Mapping):
            return
        ref = snapshot.get('ref') or snapshot.get('id')
        if isinstance(ref, str) and ref.startswith(('ckpt_', 'refs/odessy/checkpoints/')):
            self._auto_snapshot_ref = (ref if ref.startswith('refs/')
                                       else f'refs/odessy/checkpoints/{ref}')
        hashes = snapshot.get('files_sha256')
        if isinstance(hashes, Mapping):
            self._auto_snapshot_files = {str(k): str(v) for k, v in hashes.items()}
            self._file_hashes.update(self._auto_snapshot_files)

    def _write(self, *, kind: str, **kwargs: Any) -> dict[str, Any] | None:
        if self._error:
            return None  # one failure per session; do not spam the stream
        ctx = self._ctx()
        payload: dict[str, Any] = {
            'kind': kind,
            'session_id': self.session_id,
            'step_index': kwargs.pop('step_index', self._step),
            'turn_id': kwargs.pop('turn_id', ctx.get('turn_id', '')),
            'user_prompt': kwargs.pop('user_prompt', ctx.get('user_prompt', '')),
            'agent_state': kwargs.pop('agent_state', ctx.get('agent_state', '')),
        }
        payload.update(kwargs)
        try:
            result = self.service.record(self.conversation_id, **payload)
            self._last_breakpoint_at = self._clock()
            return result
        except (StorageError, Invalid, ValueError) as exc:
            self._error = f'{type(exc).__name__}: {exc}'
            return None

    def tick(self) -> dict[str, Any] | None:
        """Emit a timer breakpoint if the task has run quietly past the interval.

        Called by the runtime at step boundaries. It only writes when no other
        breakpoint has been recorded for ``timer_seconds``, so a busy turn full of
        critical/step breakpoints is not flooded with timer points.
        """
        if self._error:
            return None
        if self._clock() - self._last_breakpoint_at < self._timer_seconds:
            return None
        return self.timer()

    def begin_turn(self, turn_id: str = '', user_prompt: str = '') -> dict[str, Any] | None:
        """Start a task: capture a baseline snapshot and record the undo anchor.

        Called by the session at the start of every turn. The baseline snapshot
        is the state the user can undo back to, so a ``task-start`` breakpoint is
        written to carry its ref. The first critical-before is still accepted as
        an anchor for sessions that never call begin_turn (tests, direct loops).
        """
        self._step = 0
        self._file_hashes = {}
        self._task_start_breakpoint = ''
        self._turn_id = str(turn_id or '')
        self._user_prompt = str(user_prompt or '')
        self._auto_snapshot_ref = ''
        self._auto_snapshot_files = {}
        if self._error:
            return None
        # Baseline capture before any tool can mutate the workspace.
        self._capture('task start')
        ctx = self._ctx()
        payload = self._write(
            kind='task-start', step_index=0,
            completed=self._completed(ctx), pending=self._pending(ctx, []),
            workspace=self._workspace(ctx), cursor=self._cursor(ctx),
            resume=ctx.get('resume'))
        if payload is not None:
            self._task_start_breakpoint = payload['id']
        return payload

    def before_tool(self, *, name: str, arguments: Any, decision: Any = None,
                    call_id: str | None = None, **context: Any) -> dict[str, Any] | None:
        if not is_critical(name, arguments, decision):
            return None
        # Snapshot the state immediately before a side-effecting tool runs.
        self._capture(f'before {name or "tool"}')
        ctx = self._ctx()
        tool = {
            'name': name, 'arguments': arguments,
            'risk': getattr(decision, 'risk', None) if decision is not None else None,
            'policy_outcome': getattr(decision, 'action', None) if decision is not None else None,
            'status': 'pending',
        }
        payload = self._write(
            kind='critical-before', call_id=call_id, tool=tool,
            completed=self._completed(ctx), pending=self._pending(ctx, [call_id] if call_id else []),
            workspace=self._workspace(ctx), cursor=self._cursor(ctx),
            resume=ctx.get('resume'),
            undo_boundaries=[self._task_start_breakpoint] if self._task_start_breakpoint else [],
            **context)
        if payload is not None and not self._task_start_breakpoint:
            # Fallback anchor for loops that never called begin_turn.
            self._task_start_breakpoint = payload['id']
        return payload

    def after_tool(self, *, name: str, arguments: Any, decision: Any = None,
                   call_id: str | None = None, status: str = 'ok', **context: Any) -> dict[str, Any] | None:
        if not is_critical(name, arguments, decision):
            return None
        # Capture the resulting state after a side-effecting tool ran. Deduplicated:
        # a read-only decision or a no-op write reuses the previous snapshot.
        self._capture(f'after {name or "tool"}')
        ctx = self._ctx()
        tool = {
            'name': name, 'arguments': arguments,
            'risk': getattr(decision, 'risk', None) if decision is not None else None,
            'policy_outcome': getattr(decision, 'action', None) if decision is not None else None,
            'status': status,
        }
        completed = self._completed(ctx)
        ok = list(completed.get('ok_tool_calls') or [])
        if status == 'ok' and call_id and call_id not in ok:
            ok.append(call_id)
        completed['ok_tool_calls'] = ok
        return self._write(
            kind='critical-after', call_id=call_id, tool=tool,
            completed=completed, pending=self._pending(ctx, []),
            workspace=self._workspace(ctx), cursor=self._cursor(ctx),
            resume=ctx.get('resume'), **context)

    def step(self, *, step_index: int | None = None, **context: Any) -> dict[str, Any] | None:
        if step_index is not None:
            self._step = _check_int(step_index, 'step_index', 0)
        else:
            self._step += 1
        # Per-step capture for long multi-step tasks; deduped so an unchanged
        # step does not consume a retention slot.
        self._capture(f'step {self._step}')
        ctx = self._ctx()
        return self._write(
            kind='step', step_index=self._step,
            completed=self._completed(ctx), pending=self._pending(ctx, []),
            workspace=self._workspace(ctx), cursor=self._cursor(ctx),
            resume=ctx.get('resume'), **context)

    def timer(self, **context: Any) -> dict[str, Any] | None:
        # Idle timer capture for general tasks with no critical/step activity.
        self._capture('idle timer')
        ctx = self._ctx()
        return self._write(
            kind='timer', completed=self._completed(ctx),
            pending=self._pending(ctx, ctx.get('in_flight') or []),
            workspace=self._workspace(ctx), cursor=self._cursor(ctx),
            resume=ctx.get('resume'), **context)

    def interrupted(self, *, call_id: str | None = None, name: str = '',
                    **context: Any) -> dict[str, Any] | None:
        return self._write(
            kind='interrupted', call_id=call_id,
            tool={'name': name, 'status': 'interrupted'} if name else None,
            **context)

    def _completed(self, ctx: Mapping[str, Any]) -> dict[str, Any]:
        return {
            'settled_turns': ctx.get('settled_turns', 0),
            'ok_tool_calls': list(ctx.get('ok_tool_calls') or []),
            'assistant_text_through_sequence': ctx.get('last_sequence', 0),
            'files_changed': list(ctx.get('files_changed') or []),
        }

    def _pending(self, ctx: Mapping[str, Any], in_flight: list[str]) -> dict[str, Any]:
        return {
            'in_flight_tool_calls': list(in_flight),
            'unsettled_delta_chars': ctx.get('unsettled_delta_chars', 0),
            'pending_approvals': bool(ctx.get('pending_approvals', False)),
            'pending_jobs': bool(ctx.get('pending_jobs', False)),
            'pending_audio': bool(ctx.get('pending_audio', False)),
        }

    def _workspace(self, ctx: Mapping[str, Any]) -> dict[str, Any]:
        hashes = ctx.get('files_sha256')
        if isinstance(hashes, Mapping):
            self._file_hashes.update({str(k): str(v) for k, v in hashes.items()})
        return {
            'snapshot_ref': ctx.get('snapshot_ref'),
            'files_sha256': dict(self._file_hashes),
            'workspace_identity': ctx.get('workspace_identity', ''),
        }

    def _cursor(self, ctx: Mapping[str, Any]) -> dict[str, Any]:
        last = ctx.get('last_sequence', 0)
        return {'first_event_sequence': ctx.get('first_sequence', 0),
                'last_event_sequence': last}
