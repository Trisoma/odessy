"""Lifecycle adapters for the shared session owner (06).

The service and routes never learn about live controllers. This module is the thin
seam 06 wires into ``session.py`` / ``runtime/agent_loop.py``:

Durability relative to dispatch (contract v1, "01 — transactional storage and history"):

* the admitted user message is appended **before** the model starts, and a failed
  append raises :class:`~.store.StorageError`, so the caller must refuse the turn
  (never dispatch what was not saved);
* a ``tool_call`` is appended **before** the tool has an effect, a ``tool_result``
  **after** it returns; a crash in between leaves a visible unknown/interrupted
  record and the tool is never re-run automatically;
* settled assistant text is appended after the turn completes. Streaming deltas are
  transient: they are deduplicated by deterministic event ids and are not listed or
  exported, so a reconnect cannot duplicate them;
* after a restart, :func:`settle_after_restart` marks unfinished assistant streams
  and tool calls as interrupted/unknown with the partial text that was observed.

Adoption of a live, transient session is explicit and idempotent: it copies only
completed exchanges under deterministic event ids, never touches the running turn,
and can be repeated without duplicating history.
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any, Mapping

from odessy.redaction import redact_text

from .service import ConversationService
from .store import ConversationStore, StorageError, utc_now

__all__ = ['ConversationRecorder', 'ConversationLog', 'adopt_live_session',
           'settle_after_restart']


def _public_text(value: Any) -> str:
    return redact_text(value if isinstance(value, str) else str(value))


class ConversationRecorder:
    """One live session's write path into its durable conversation."""

    def __init__(self, service: ConversationService, conversation_id: str, *,
                 session_id: str | None = None, clock=utc_now):
        self.service = service
        self.store: ConversationStore = service.store
        self.conversation_id = conversation_id
        self.session_id = session_id
        self.clock = clock
        self.turn_id: str | None = None
        self._delta = 0

    # -- turn boundary ---------------------------------------------------

    def begin_turn(self, text: str) -> str:
        """Durably record the admitted user message; raise if it was not saved."""
        self.turn_id = uuid.uuid4().hex
        self._delta = 0
        self.store.append(self.conversation_id, event_id=f'{self.turn_id}:user',
                          kind='user', payload={'text': _public_text(text)},
                          created_at=self.clock())
        return self.turn_id

    def delta(self, text: str) -> None:
        """Transient streamed text; best effort and deduplicated by event id."""
        if self.turn_id is None or not text:
            return
        self._delta += 1
        try:
            self.store.append(self.conversation_id,
                              event_id=f'{self.turn_id}:delta:{self._delta}',
                              kind='assistant_delta',
                              payload={'text': _public_text(text)},
                              created_at=self.clock())
        except StorageError:
            return  # transient: the settled answer still carries the truth

    def public_event(self, event_type: str, data: Mapping[str, Any]) -> None:
        """Retain only already-public bounded metadata, never tool arguments/results."""
        if self.turn_id is None:
            return
        if event_type == 'delta':
            self.delta(str(data.get('text') or '')[:16384])
            return
        fields = {
            'tool': ('phase', 'name', 'status', 'call_id', 'batch_id'),
            'policy': ('name', 'outcome', 'risk', 'call_id'),
            'hook': ('event', 'name', 'hook', 'decision', 'failed', 'action'),
            'progress': ('message',),
            'stopped': ('reason', 'status'),
            'error': ('message', 'error_type'),
        }.get(event_type)
        if fields is None:
            return
        safe = {key: _public_text(data[key])[:2048] for key in fields if key in data}
        self.store.append(self.conversation_id, event_id=f'public:{uuid.uuid4().hex}',
                          kind='activity', payload={'type': event_type, 'data': safe},
                          created_at=self.clock())

    def settle(self, text: str) -> None:
        if self.turn_id is None:
            raise RuntimeError('No admitted turn to settle')
        self.store.append(self.conversation_id, event_id=f'{self.turn_id}:assistant',
                          kind='assistant', payload={'text': _public_text(text)},
                          created_at=self.clock())

    def interrupted(self, reason: str, partial: str = '') -> None:
        if self.turn_id is None:
            return
        self.store.append(self.conversation_id, event_id=f'{self.turn_id}:interrupted',
                          kind='interrupted',
                          payload={'reason': _public_text(reason),
                                   'outcome': 'unknown',
                                   'text': _public_text(partial)},
                          created_at=self.clock())

    # -- tools and attachments -------------------------------------------

    def tool_call(self, call_id: str, name: str) -> None:
        self.store.append(self.conversation_id, event_id=f'{call_id}:call',
                          kind='tool_call',
                          payload={'call_id': _public_text(call_id),
                                   'name': _public_text(name)},
                          created_at=self.clock())

    def tool_result(self, call_id: str, name: str, status: str) -> None:
        self.store.append(self.conversation_id, event_id=f'{call_id}:result',
                          kind='tool_result',
                          payload={'call_id': _public_text(call_id),
                                   'name': _public_text(name),
                                   'status': _public_text(status)},
                          created_at=self.clock())

    def activity(self, text: str, *, name: str | None = None) -> None:
        event_id = f'activity:{uuid.uuid4().hex}'
        self.store.append(self.conversation_id, event_id=event_id, kind='activity',
                          payload={'text': _public_text(text),
                                   **({'name': _public_text(name)} if name else {})},
                          created_at=self.clock())

    def attachment(self, *, name: str, size: int, media_type: str | None = None) -> None:
        """Metadata only: adoption/attachment never copies or authorizes a file."""
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError('Attachment size must be a non-negative integer')
        self.store.append(self.conversation_id,
                          event_id=f'attachment:{uuid.uuid4().hex}', kind='attachment',
                          payload={'name': _public_text(name), 'bytes': size,
                                   'media_type': _public_text(media_type or '')},
                          created_at=self.clock())


class ConversationLog:
    """Maps the live session event stream onto durable records.

    ``observe(event_type, data)`` is the single hook 06 can call from the session's
    observer. User admission and tool calls must NOT rely on the event stream: they
    are written by :class:`ConversationRecorder` before dispatch, because a crash
    before emission would otherwise lose the record.
    """

    def __init__(self, recorder: ConversationRecorder):
        self.recorder = recorder

    def observe(self, event_type: str, data: Mapping[str, Any] | None = None) -> None:
        payload = data if isinstance(data, Mapping) else {}
        if event_type == 'transcript':
            role = payload.get('role')
            text = payload.get('text') or ''
            if role == 'user':
                return  # already durable before dispatch
            if role == 'assistant':
                self.recorder.delta(text)
            return
        if event_type == 'tool':
            phase = payload.get('phase')
            call_id = payload.get('call_id')
            if not isinstance(call_id, str):
                return
            if phase == 'started':
                self.recorder.tool_call(call_id, str(payload.get('name') or 'tool'))
            elif phase == 'finished':
                self.recorder.tool_result(call_id, str(payload.get('name') or 'tool'),
                                         str(payload.get('status') or 'ok'))


def settle_after_restart(store: ConversationStore, *, clock=utc_now) -> int:
    """Mark unfinished assistant streams and tool calls as interrupted/unknown.

    Idempotent: the settlement event id is derived from the last stored sequence, so
    repeating it after another restart cannot duplicate the record. Nothing is
    retried and no tool is re-run.
    """
    settled = 0
    for conversation_id in store.all_conversation_ids():
        events = store.raw_events(conversation_id)
        if not events:
            continue
        last_sequence = events[-1]['sequence']
        open_tools: dict[str, dict[str, Any]] = {}
        reported_tools: set[str] = set()
        partial: list[str] = []
        open_stream = False
        last_delta_at = -1
        last_stream_settlement = -1
        for index, event in enumerate(events):
            if event['kind'] == 'assistant_delta':
                partial.append(str(event['payload'].get('text') or ''))
                open_stream = True
                last_delta_at = index
            elif event['kind'] == 'assistant' or event['kind'] == 'user':
                partial.clear()
                open_stream = False
            elif event['kind'] == 'tool_call':
                call_id = event['payload'].get('call_id')
                if isinstance(call_id, str):
                    open_tools[call_id] = event
            elif event['kind'] == 'tool_result':
                open_tools.pop(str(event['payload'].get('call_id') or ''), None)
            elif event['kind'] == 'interrupted':
                reported_tools.update(str(call) for call in
                                      (event['payload'].get('tool_calls') or []))
                if event['payload'].get('text'):
                    last_stream_settlement = index
        open_tools = {call: event for call, event in open_tools.items()
                      if call not in reported_tools}
        if open_tools:
            store.append(conversation_id, event_id=f'interrupted:tools:{last_sequence}',
                         kind='interrupted',
                         payload={'reason': 'restart', 'outcome': 'unknown',
                                  'tool_calls': [str(call) for call in open_tools]},
                         created_at=clock())
            settled += 1
        if open_stream and last_stream_settlement < last_delta_at:
            store.append(conversation_id, event_id=f'interrupted:stream:{last_sequence}',
                         kind='interrupted',
                         payload={'reason': 'restart', 'outcome': 'unknown',
                                  'text': ''.join(partial)},
                         created_at=clock())
            settled += 1
    return settled


def adopt_live_session(service: ConversationService, *, project_id: str, session_id: str,
                       engine: str, profile: str, turns: list[Mapping[str, Any]],
                       title: str = 'Imported conversation', clock=utc_now) -> dict[str, Any]:
    """Adopt a transient live session without losing or replaying its live turn.

    Only completed ``{user, assistant}`` exchanges are copied, under deterministic ids
    derived from the live session id, so re-running adoption converges. The live
    session itself is untouched and its in-flight turn is not part of the copy; it
    becomes durable when its turn settles through :class:`ConversationRecorder`.
    """
    conversation_id = hashlib.sha256(f'live:{session_id}'.encode()).hexdigest()[:32]
    try:
        row = service.store.get(conversation_id)
    except StorageError as exc:
        if getattr(exc, 'code', '') != 'not_found':
            raise
        row = service.store.create(conversation_id=conversation_id, project_id=project_id,
                                   engine=engine, profile=profile, title=title)
    for index, turn in enumerate(turns):
        user = turn.get('user') if isinstance(turn, Mapping) else None
        assistant = turn.get('assistant') if isinstance(turn, Mapping) else None
        if isinstance(user, str) and user:
            service.store.append(conversation_id, event_id=f'live:{session_id}:{index}:user',
                                 kind='user', payload={'text': _public_text(user)},
                                 created_at=clock())
        if isinstance(assistant, str) and assistant:
            service.store.append(conversation_id,
                                 event_id=f'live:{session_id}:{index}:assistant',
                                 kind='assistant',
                                 payload={'text': _public_text(assistant)},
                                 created_at=clock())
    return service.get(conversation_id)
