"""Conversation service: identity, revisions and continuation semantics on the store.

The store owns durable rows; this service owns the wire-facing decisions that must
stay consistent across engines and restarts:

* durable conversation UUIDs and their project association;
* ``continuation = continuable | history_only``: only the legacy engine can be
  genuinely continued here, and only when the shared session lifecycle owner (06)
  has bound a continuation handler. A Harness conversation is durable read-only
  history; it is never replayed into a new session and called restored.
* resume serialization: two concurrent resumes of one conversation cannot create
  two live sessions.

Nothing in this module starts a model, a process, capture or a download.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import uuid
from typing import Any, Callable, Mapping

from .store import (Conflict, ConversationStore, ENGINES, Invalid, MAX_TITLE, StorageError,
                    _check_int)

DEFAULT_TITLE = 'New conversation'


class Unsupported(StorageError):
    """A real feature the current composition cannot provide; 501, never a fake."""
    code = 'unsupported'
    status = 501


class ConversationService:
    def __init__(self, store: ConversationStore, *,
                 continuation: Callable[[Mapping[str, Any]], Any] | None = None,
                 settings: Callable[[], Mapping[str, Any]] | None = None,
                 service_instance_id: str = ''):
        self.store = store
        #: Bound by the shared owner (06): prepares a live legacy session from
        #: ``store.prepare_resume`` data. Never created here.
        self._continuation = continuation
        self._settings = settings or (lambda: {'engine': 'legacy', 'profile': 'default'})
        self.service_instance_id = service_instance_id
        self._resume_locks: dict[str, asyncio.Lock] = {}

    # -- projects --------------------------------------------------------

    def projects(self) -> dict[str, Any]:
        return {'version': 1, 'projects': [dict(item) for item in self.store.projects()]}

    def create_project(self, label: str) -> dict[str, Any]:
        return dict(self.store.create_project(label))

    # -- conversations ---------------------------------------------------

    def _row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return {'version': 1, 'id': row['id'], 'project_id': row['project_id'],
                'engine': row['engine'], 'profile': row['profile'], 'title': row['title'],
                'pinned': bool(row['pinned']), 'archived': bool(row['archived']),
                'revision': row['revision'], 'created_at': row['created_at'],
                'updated_at': row['updated_at'],
                'continuation': self.continuation_kind(row['engine'])}

    def continuation_kind(self, engine: str) -> str:
        if engine != 'legacy':
            # Harness durable history is required; native restore is not supported.
            return 'history_only'
        return 'continuable' if self._continuation is not None else 'history_only'

    def create(self, *, project_id: str, title: str | None = None) -> dict[str, Any]:
        configured = self._settings()
        if not isinstance(configured, Mapping):
            raise Invalid('Configured engine metadata is unavailable')
        engine = configured.get('engine', 'legacy')
        if engine not in ENGINES:
            raise Invalid('Configured engine is not supported')
        profile = configured.get('profile', 'default')
        if not isinstance(profile, str) or not 1 <= len(profile) <= 200:
            raise Invalid('Configured profile metadata is unavailable')
        chosen = DEFAULT_TITLE if title is None else title
        if not isinstance(chosen, str):
            raise Invalid('title must be a string')
        chosen = chosen.strip() or DEFAULT_TITLE
        if len(chosen) > MAX_TITLE:
            raise Invalid(f'title must be at most {MAX_TITLE} characters')
        row = self.store.create(conversation_id=uuid.uuid4().hex, project_id=project_id,
                                engine=engine, profile=profile, title=chosen)
        return self._row(row)

    def get(self, conversation_id: str) -> dict[str, Any]:
        return self._row(self.store.get(conversation_id))

    def list(self, *, project_id: str | None = None, archived: bool = False,
             cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        page = self.store.list(project_id=project_id, archived=archived,
                               cursor=cursor, limit=limit)
        return {'version': 1,
                'conversations': [self._row(row) for row in page['conversations']],
                'next_cursor': page['next_cursor']}

    def messages(self, conversation_id: str, *, before: int | None = None,
                 limit: int = 100) -> dict[str, Any]:
        page = self.store.messages(conversation_id, before=before, limit=limit)
        return {'version': 1, **page}

    def patch(self, conversation_id: str, *, expected_revision: int,
              title: str | None = None, pinned: bool | None = None,
              archived: bool | None = None) -> dict[str, Any]:
        row = self.store.patch(conversation_id, expected_revision=expected_revision,
                              title=title, pinned=pinned, archived=archived)
        return self._row(row)

    def search(self, *, query: str, project_id: str | None = None,
               cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        page = self.store.search(query=query, project_id=project_id,
                                 cursor=cursor, limit=limit)
        return {'version': 1, 'matches': page['matches'], 'next_cursor': page['next_cursor']}

    def export(self, conversation_id: str) -> dict[str, Any]:
        document = self.store.export(conversation_id)
        conversation = self._row(document['conversation'])
        return {'version': 1, 'conversation': conversation, 'events': document['events']}

    def delete(self, conversation_id: str, *, expected_revision: int) -> None:
        self.store.delete(conversation_id, expected_revision=expected_revision)
        self._resume_locks.pop(conversation_id, None)

    def prepare_resume(self, conversation_id: str) -> dict[str, Any]:
        prepared = self.store.prepare_resume(conversation_id)
        return {'version': 1, 'conversation': self._row(prepared['conversation']),
                'turns': prepared['turns'], 'revision': prepared['revision'],
                'unfinished': prepared['unfinished'], 'open_user': prepared['open_user']}

    def register_before_delete(self, callback: Callable[[str], None]) -> None:
        """Wave 2 registers derived-data invalidation here (runs before deletion)."""
        self.store.register_before_delete(callback)

    async def resume(self, conversation_id: str, *, expected_revision: int) -> dict[str, Any]:
        expected_revision = _check_int(expected_revision, 'expected_revision', 0)
        row = self.store.get(conversation_id)
        if row['engine'] != 'legacy':
            raise Unsupported('This engine has durable history only; it cannot be resumed')
        if self._continuation is None:
            raise Unsupported('Continuation requires the shared session lifecycle owner')
        lock = self._resume_locks.setdefault(conversation_id, asyncio.Lock())
        if lock.locked():
            # The other call is preparing the one live session; refuse rather than
            # create a second controller for the same durable conversation.
            raise Conflict('A resume of this conversation is already in progress')
        async with lock:
            prepared = self.store.prepare_resume(conversation_id)
            if prepared['revision'] != expected_revision:
                raise Conflict('Conversation changed; reload before resuming')
            result = self._continuation(prepared)
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, Mapping) or not isinstance(result.get('session_id'), str):
                raise StorageError('Continuation owner did not return a live session id')
            return {'version': 1, 'conversation_id': conversation_id,
                    'session_id': result['session_id'],
                    'service_instance_id': self.service_instance_id,
                    'continuation': 'continuable'}


def build_service(*, path: str | os.PathLike[str] | None = None,
                  store: ConversationStore | None = None,
                  continuation: Callable[[Mapping[str, Any]], Any] | None = None,
                  settings: Callable[[], Mapping[str, Any]] | None = None) -> ConversationService:
    """Factory used by the integrator (06); tests pass their own temporary path."""
    if store is None:
        from .store import default_store_path
        store = ConversationStore(default_store_path() if path is None else path)
    return ConversationService(store, continuation=continuation, settings=settings)
