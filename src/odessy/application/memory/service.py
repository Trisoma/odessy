"""Explicit, user-created project memory (Wave 2, B11).

Only what the user wrote is stored. There is no automatic extraction, no model
call, and no cross-project read: every operation filters by ``project_id`` and the
route's project must own the fact. Disabled or deleted facts disappear from
:meth:`MemoryService.enabled_facts`, so the next prepared request cannot include
them; no separate request cache is kept.
"""
from __future__ import annotations

import sqlite3
import uuid

from ..conversations.store import Conflict, ConversationStore, Invalid, NotFound, StorageError
from ..conversations.store import _check_id, _check_int, utc_now

MAX_TEXT = 2000
MAX_ENABLED = 100
PROJECT_LIMIT = 500


def _text(value, name='text'):
    if not isinstance(value, str):
        raise Invalid(f'{name} must be a string')
    text = value.strip()
    if not 1 <= len(text) <= MAX_TEXT:
        raise Invalid(f'{name} must be 1-{MAX_TEXT} characters')
    return text


class MemoryService:
    def __init__(self, store: ConversationStore, *, clock=utc_now):
        self.store = store
        self.clock = clock

    def _project(self, db, project_id):
        _check_id(project_id, 'project_id')
        if db.execute('SELECT 1 FROM projects WHERE id = ?', (project_id,)).fetchone() is None:
            raise NotFound('Project not found')
        return project_id

    def _wire(self, row):
        return {'version': 1, 'id': row['id'], 'project_id': row['project_id'],
                'text': row['text'], 'enabled': bool(row['enabled']),
                'revision': row['revision'], 'created_at': row['created_at'],
                'updated_at': row['updated_at'], 'source': row['source']}

    def list(self, project_id):
        def work(db):
            self._project(db, project_id)
            return [self._wire(row) for row in db.execute(
                'SELECT * FROM memory_facts WHERE project_id = ? '
                'ORDER BY created_at, id', (project_id,))]
        with self.store.connection() as db:
            return work(db)

    def create(self, project_id, text):
        text = _text(text)
        now = self.clock()
        fact = {'id': uuid.uuid4().hex, 'project_id': None, 'text': text,
                'enabled': True, 'revision': 0, 'source': 'user',
                'created_at': now, 'updated_at': now}

        def work(db):
            self._project(db, project_id)
            count = db.execute('SELECT COUNT(*) FROM memory_facts WHERE project_id = ?',
                               (project_id,)).fetchone()[0]
            if count >= PROJECT_LIMIT:
                raise Conflict('This project has too many saved memory facts')
            fact['project_id'] = project_id
            db.execute('INSERT INTO memory_facts (id, project_id, text, enabled, revision, '
                       'source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)',
                       (fact['id'], project_id, text, 1, 0, 'user', now, now))
            return self._wire(db.execute('SELECT * FROM memory_facts WHERE id = ?',
                                         (fact['id'],)).fetchone())

        with self.store.connection() as db, db:
            return work(db)

    def _fact(self, db, project_id, fact_id):
        _check_id(fact_id, 'memory_id')
        row = db.execute('SELECT * FROM memory_facts WHERE id = ?', (fact_id,)).fetchone()
        if row is None or row['project_id'] != project_id:
            raise NotFound('Memory fact not found')
        return dict(row)

    def patch(self, project_id, fact_id, *, expected_revision, text=None, enabled=None):
        expected_revision = _check_int(expected_revision, 'expected_revision')
        if text is not None:
            text = _text(text)
        if enabled is not None and not isinstance(enabled, bool):
            raise Invalid('enabled must be a boolean')
        now = self.clock()

        def work(db):
            self._project(db, project_id)
            row = self._fact(db, project_id, fact_id)
            if row['revision'] != expected_revision:
                raise Conflict('Memory fact changed; reload before saving')
            db.execute('UPDATE memory_facts SET text = ?, enabled = ?, revision = revision + 1, '
                       'updated_at = ? WHERE id = ?',
                       (text if text is not None else row['text'],
                        int(enabled) if enabled is not None else row['enabled'],
                        now, fact_id))
            return self._wire(db.execute('SELECT * FROM memory_facts WHERE id = ?',
                                         (fact_id,)).fetchone())

        with self.store.connection() as db, db:
            return work(db)

    def delete(self, project_id, fact_id, *, expected_revision):
        expected_revision = _check_int(expected_revision, 'expected_revision')

        def work(db):
            self._project(db, project_id)
            row = self._fact(db, project_id, fact_id)
            if row['revision'] != expected_revision:
                raise Conflict('Memory fact changed; reload before deleting')
            db.execute('DELETE FROM memory_facts WHERE id = ?', (fact_id,))

        with self.store.connection() as db, db:
            work(db)

    def enabled_facts(self, project_id, *, limit=MAX_ENABLED):
        limit = _check_int(limit, 'limit', 1)
        if limit > MAX_ENABLED:
            raise Invalid(f'At most {MAX_ENABLED} facts may be injected')

        def work(db):
            self._project(db, project_id)
            return [{'id': row['id'], 'text': row['text'], 'revision': row['revision']}
                    for row in db.execute(
                        'SELECT * FROM memory_facts WHERE project_id = ? AND enabled = 1 '
                        'ORDER BY updated_at DESC, id DESC LIMIT ?', (project_id, limit))]

        with self.store.connection() as db:
            return work(db)

    def forget_conversation(self, conversation_id):
        """Memory is project-scoped, so no rows belong to one conversation."""
        _check_id(conversation_id)
        return 0
