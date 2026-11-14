"""Transactional, versioned store for durable conversations and transcript history.

Design notes (contract v1, section "01 — transactional storage and history"):

* One SQLite database per user profile, ``conversations.sqlite3``; tests inject a
  temporary path. ``usage-history.sqlite3`` and telemetry databases are untouched.
* WAL, ``foreign_keys=ON``, a bounded busy timeout and serialized writes. No
  connection is shared across threads: every operation opens its own connection.
* ``PRAGMA user_version`` belongs to this module only. Wave 2 extension migrations
  run through :meth:`ConversationStore.connection` after core setup, never touching
  the core pragma.
* ``append`` is idempotent by ``event_id``: replaying the same event (a reconnecting
  client, a restarted recorder) returns the stored row instead of a duplicate.
* A future or corrupt database is rejected, never reset. A failed write raises and
  is never reported as saved.
"""
from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Callable, Mapping

SCHEMA_VERSION = 1
MAX_TITLE = 120
MAX_LABEL = 120
MAX_PAYLOAD = 256 * 1024
MAX_EXPORT = 2 * 1024 * 1024
MAX_QUERY = 512
MAX_SNIPPET = 400
KINDS = ('user', 'assistant_delta', 'assistant', 'tool_call', 'tool_result',
         'activity', 'attachment', 'interrupted')
ENGINES = ('legacy', 'harness')
_TIMESTAMP = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$')
_ID = re.compile(r'^[0-9a-f]{32}$')


class StorageError(Exception):
    """The store could not read or write; nothing was acknowledged as saved."""
    code = 'storage_failure'
    status = 503


class NotFound(StorageError):
    code = 'not_found'
    status = 404


class Conflict(StorageError):
    """Revision mismatch, concurrent duplicate or scope mismatch."""
    code = 'conflict'
    status = 409


class Invalid(StorageError):
    code = 'invalid'
    status = 400


class PayloadTooLarge(Invalid):
    code = 'too_large'
    status = 413


class SchemaTooNew(StorageError):
    code = 'schema_too_new'
    status = 503


class CorruptDatabase(StorageError):
    code = 'corrupt'
    status = 503


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def default_store_path() -> Path:
    """Beside an explicit ``ODESSY_CONFIG_FILE``, else the existing profile convention."""
    explicit = os.environ.get('ODESSY_CONFIG_FILE')
    if explicit:
        return Path(explicit).expanduser().parent / 'conversations.sqlite3'
    return Path.home() / '.config' / 'odessy' / 'conversations.sqlite3'


def _encode_cursor(values: Mapping[str, Any]) -> str:
    raw = json.dumps(values, separators=(',', ':'), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def _decode_cursor(value: str) -> Mapping[str, Any]:
    if not isinstance(value, str) or not 1 <= len(value) <= 1024:
        raise Invalid('Invalid cursor')
    padded = value + '=' * (-len(value) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode())
        data = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise Invalid('Invalid cursor') from None
    if not isinstance(data, dict):
        raise Invalid('Invalid cursor')
    return data


def _check_text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise Invalid(f'{name} must be a string')
    text = value.strip()
    if not 1 <= len(text) <= limit:
        raise Invalid(f'{name} must be 1-{limit} characters')
    return text


def _check_int(value: Any, name: str, minimum: int = 0) -> int:
    # bool is an int subclass; the contract rejects bool-as-int explicitly.
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise Invalid(f'{name} must be an integer >= {minimum}')
    return value


def _check_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise Invalid(f'{name} must be a boolean')
    return value


def _check_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not _TIMESTAMP.match(value):
        raise Invalid('created_at must be a UTC ISO8601 timestamp ending in Z')
    return value


def _check_id(value: Any, name: str = 'conversation_id') -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise Invalid(f'{name} must be a 32-character lowercase hex id')
    return value


def _searchable(kind: str, payload: Mapping[str, Any]) -> str | None:
    """Text the user can find: settled messages and named tool/activity entries.

    ``assistant_delta`` is deliberately absent: streamed deltas are the same text the
    settled ``assistant`` record holds, and the transcript must not duplicate them.
    """
    if kind in ('user', 'assistant'):
        text = payload.get('text')
        return text if isinstance(text, str) else None
    if kind == 'interrupted':
        text = payload.get('text')
        return text if isinstance(text, str) else None
    if kind in ('tool_call', 'tool_result'):
        name = payload.get('name')
        return name if isinstance(name, str) else None
    if kind == 'activity':
        text = payload.get('text') or payload.get('name')
        return text if isinstance(text, str) else None
    return None


def _fts_query(query: str) -> str:
    """Quote every term so FTS5 operators inside user text cannot be interpreted."""
    terms = [term for term in re.split(r'\s+', query) if term]
    if not terms:
        raise Invalid('Search query is empty')
    quoted = []
    for term in terms[:16]:
        cleaned = ''.join(ch for ch in term if ch.isprintable())
        if cleaned:
            quoted.append('"' + cleaned.replace('"', '""') + '"')
    if not quoted:
        raise Invalid('Search query is empty')
    return ' AND '.join(quoted)


def _snippet(text: str, query: str) -> str:
    lowered, needle = text.lower(), None
    for term in re.split(r'\s+', query):
        if term and term.lower() in lowered:
            needle = lowered.index(term.lower())
            break
    if needle is None:
        return text[:MAX_SNIPPET]
    start = max(0, needle - 120)
    return text[start:start + MAX_SNIPPET]


class ConversationStore:
    """SQLite implementation of the frozen ``ConversationStore`` protocol."""

    def __init__(self, path: str | os.PathLike[str], *, fault: Callable[[str], None] | None = None):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._closed = False
        self._fts: bool | None = None
        #: Wave 2 derived data (summaries/search/context use) must be invalidated
        #: before a conversation's rows disappear.
        self._before_delete: list[Callable[[str], None]] = []
        #: Test seam: invoked with the operation name before a write transaction
        #: commits. Raising models a disk/IO failure without filling a real disk.
        self._fault = fault
        self._prepare()

    # -- lifecycle -------------------------------------------------------

    def _prepare(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.path.exists():
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
        with self._connect() as db:
            self._initialize(db)

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise StorageError('Conversation store is closed')
        try:
            db = sqlite3.connect(self.path, timeout=5)
        except sqlite3.DatabaseError as exc:
            raise CorruptDatabase(f'Conversation database cannot be opened: {exc}') from exc
        db.row_factory = sqlite3.Row
        try:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            db.execute('PRAGMA foreign_keys = ON')
            db.execute('PRAGMA busy_timeout = 5000')
            try:
                db.execute('PRAGMA journal_mode = WAL')
            except sqlite3.DatabaseError:
                pass  # Some network filesystems refuse WAL; correctness is kept.
        except sqlite3.DatabaseError as exc:
            db.close()
            raise CorruptDatabase(f'Conversation database is not usable: {exc}') from exc
        return db

    @contextmanager
    def connection(self):
        """Serialized access to the same database for extension modules (Wave 2)."""
        db = self._connect()
        try:
            self._initialize(db)
            yield db
        except sqlite3.DatabaseError as exc:
            raise self._translate(exc) from exc
        finally:
            db.close()

    def _initialize(self, db: sqlite3.Connection) -> None:
        try:
            version = db.execute('PRAGMA user_version').fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise self._translate(exc) from exc
        if version > SCHEMA_VERSION:
            raise SchemaTooNew(
                f'Conversation database schema {version} is newer than supported {SCHEMA_VERSION}')
        if version == SCHEMA_VERSION:
            return
        with self._lock:
            try:
                db.execute('BEGIN IMMEDIATE')
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version > SCHEMA_VERSION:
                    db.rollback()
                    raise SchemaTooNew(
                        f'Conversation database schema {version} is newer than supported {SCHEMA_VERSION}')
                if version == 0:
                    db.executescript('''
                        CREATE TABLE IF NOT EXISTS projects (
                            id TEXT PRIMARY KEY,
                            label TEXT NOT NULL,
                            created_at TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS conversations (
                            id TEXT PRIMARY KEY,
                            project_id TEXT NOT NULL REFERENCES projects(id),
                            engine TEXT NOT NULL,
                            profile TEXT NOT NULL,
                            title TEXT NOT NULL,
                            pinned INTEGER NOT NULL DEFAULT 0,
                            archived INTEGER NOT NULL DEFAULT 0,
                            revision INTEGER NOT NULL DEFAULT 0,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL);
                        CREATE INDEX IF NOT EXISTS conversations_list
                            ON conversations(project_id, archived, pinned, updated_at, id);
                        CREATE TABLE IF NOT EXISTS conversation_events (
                            conversation_id TEXT NOT NULL
                                REFERENCES conversations(id) ON DELETE CASCADE,
                            sequence INTEGER NOT NULL,
                            event_id TEXT NOT NULL,
                            kind TEXT NOT NULL,
                            payload_json TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            PRIMARY KEY (conversation_id, sequence),
                            UNIQUE (conversation_id, event_id));
                        CREATE INDEX IF NOT EXISTS conversation_events_page
                            ON conversation_events(conversation_id, kind, sequence);
                        CREATE TABLE IF NOT EXISTS schema_extensions (
                            name TEXT PRIMARY KEY,
                            version INTEGER NOT NULL);
                    ''')
                db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
                db.commit()
            except sqlite3.DatabaseError as exc:
                with _suppress():
                    db.rollback()
                raise self._translate(exc) from exc

    def close(self) -> None:
        """Release the store. Reopening the same path must see the same history."""
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    # -- error translation ----------------------------------------------

    @staticmethod
    def _translate(exc: sqlite3.DatabaseError) -> StorageError:
        message = str(exc)
        lowered = message.lower()
        if 'malformed' in lowered or 'not a database' in lowered or 'corrupt' in lowered:
            return CorruptDatabase(f'Conversation database is corrupt: {message}')
        return StorageError(f'Conversation storage failed: {message}')

    def _write(self, operation: str, work: Callable[[sqlite3.Connection], Any]) -> Any:
        with self._lock:
            db = self._connect()
            try:
                try:
                    db.execute('BEGIN IMMEDIATE')
                    if self._fault is not None:
                        self._fault(operation)
                    result = work(db)
                    db.commit()
                    return result
                except sqlite3.DatabaseError as exc:
                    with _suppress():
                        db.rollback()
                    raise self._translate(exc) from exc
                except OSError as exc:
                    with _suppress():
                        db.rollback()
                    raise StorageError(
                        f'Conversation storage failed: {exc}; nothing was saved') from exc
                except BaseException:
                    with _suppress():
                        db.rollback()
                    raise
            finally:
                db.close()

    def _read(self, work: Callable[[sqlite3.Connection], Any]) -> Any:
        db = self._connect()
        try:
            self._initialize(db)
            return work(db)
        except sqlite3.DatabaseError as exc:
            raise self._translate(exc) from exc
        finally:
            db.close()

    # -- projects --------------------------------------------------------

    def create_project(self, label: str) -> Mapping[str, Any]:
        label = _check_text(label, 'label', MAX_LABEL)
        project = {'version': 1, 'id': os.urandom(16).hex(), 'label': label,
                   'created_at': utc_now()}

        def work(db):
            db.execute('INSERT INTO projects (id,label,created_at) VALUES (?,?,?)',
                       (project['id'], project['label'], project['created_at']))
            return dict(project)

        return self._write('create_project', work)

    def projects(self) -> tuple[Mapping[str, Any], ...]:
        rows = self._read(lambda db: [dict(row) for row in db.execute(
            'SELECT id,label,created_at FROM projects ORDER BY created_at, id')])
        return tuple({'version': 1, **row} for row in rows)

    # -- conversations ---------------------------------------------------

    def _conversation(self, db: sqlite3.Connection, conversation_id: str) -> dict[str, Any]:
        row = db.execute('SELECT * FROM conversations WHERE id = ?', (conversation_id,)).fetchone()
        if row is None:
            raise NotFound('Conversation not found')
        return dict(row)

    def create(self, *, conversation_id: str, project_id: str, engine: str,
               profile: str, title: str) -> Mapping[str, Any]:
        conversation_id = _check_id(conversation_id)
        project_id = _check_id(project_id, 'project_id')
        if engine not in ENGINES:
            raise Invalid('engine must be legacy or harness')
        profile = _check_text(profile, 'profile', 200)
        title = _check_text(title, 'title', MAX_TITLE)
        now = utc_now()

        def work(db):
            project = db.execute('SELECT id FROM projects WHERE id = ?', (project_id,)).fetchone()
            if project is None:
                raise NotFound('Project not found')
            db.execute('''INSERT INTO conversations
                (id,project_id,engine,profile,title,pinned,archived,revision,created_at,updated_at)
                VALUES (?,?,?,?,?,0,0,0,?,?)''',
                       (conversation_id, project_id, engine, profile, title, now, now))
            return self._conversation(db, conversation_id)

        return self._write('create', work)

    def get(self, conversation_id: str) -> Mapping[str, Any]:
        _check_id(conversation_id)
        return self._read(lambda db: self._conversation(db, conversation_id))

    def _cursor_scope(self, project_id: str | None, archived: bool) -> dict[str, Any]:
        return {'project_id': project_id, 'archived': bool(archived)}

    def list(self, *, project_id: str | None = None, archived: bool = False,
             cursor: str | None = None, limit: int = 50) -> Mapping[str, Any]:
        if project_id is not None:
            project_id = _check_id(project_id, 'project_id')
        archived = _check_bool(archived, 'archived')
        limit = _check_int(limit, 'limit', 1)
        if limit > 100:
            raise Invalid('limit must be 1-100')
        scope = self._cursor_scope(project_id, archived)
        conditions = ['archived = ?']
        args: list[Any] = [int(archived)]
        if project_id is not None:
            conditions.append('project_id = ?')
            args.append(project_id)
        if cursor is not None:
            position = _decode_cursor(cursor)
            if {key: position.get(key) for key in scope} != scope:
                raise Conflict('Cursor does not belong to this query')
            conditions.append('(pinned < ? OR (pinned = ? AND (updated_at < ? '
                              'OR (updated_at = ? AND id < ?))))')
            args.extend((position.get('pinned'), position.get('pinned'),
                         position.get('updated_at'), position.get('updated_at'),
                         position.get('id')))
        where = ' WHERE ' + ' AND '.join(conditions)
        rows = self._read(lambda db: [dict(row) for row in db.execute(
            'SELECT * FROM conversations' + where +
            ' ORDER BY pinned DESC, updated_at DESC, id DESC LIMIT ?', (*args, limit + 1))])
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = _encode_cursor({**scope, 'pinned': last['pinned'],
                                          'updated_at': last['updated_at'], 'id': last['id']})
        return {'conversations': page, 'next_cursor': next_cursor}

    def messages(self, conversation_id: str, *, before: int | None = None,
                 limit: int = 100) -> Mapping[str, Any]:
        _check_id(conversation_id)
        limit = _check_int(limit, 'limit', 1)
        if limit > 100:
            raise Invalid('limit must be 1-100')
        if before is not None:
            before = _check_int(before, 'before', 0)

        def work(db):
            self._conversation(db, conversation_id)
            args: list[Any] = [conversation_id]
            extra = ''
            if before is not None:
                extra = ' AND sequence < ?'
                args.append(before)
            rows = [dict(row) for row in db.execute(
                'SELECT * FROM conversation_events WHERE conversation_id = ?' + extra +
                " AND kind != 'assistant_delta' ORDER BY sequence DESC LIMIT ?",
                (*args, limit + 1))]
            return rows

        rows = self._read(work)
        more = len(rows) > limit
        page = list(reversed(rows[:limit]))
        return {'conversation_id': conversation_id,
                'messages': [_event(row) for row in page],
                'next_before': page[0]['sequence'] if more and page else None}

    def append(self, conversation_id: str, *, event_id: str, kind: str,
               payload: Mapping[str, Any], created_at: str | None = None) -> Mapping[str, Any]:
        _check_id(conversation_id)
        event_id = _check_text(event_id, 'event_id', 200)
        if kind not in KINDS:
            raise Invalid('Unknown event kind')
        if not isinstance(payload, Mapping):
            raise Invalid('payload must be an object')
        encoded = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        if len(encoded.encode()) > MAX_PAYLOAD:
            raise PayloadTooLarge('Event payload exceeds 256 KiB')
        created_at = utc_now() if created_at is None else _check_timestamp(created_at)

        def work(db):
            conversation = self._conversation(db, conversation_id)
            existing = db.execute(
                'SELECT * FROM conversation_events WHERE conversation_id = ? AND event_id = ?',
                (conversation_id, event_id)).fetchone()
            if existing is not None:
                if existing['kind'] != kind or existing['payload_json'] != encoded:
                    raise Conflict('Event id already exists with different content')
                return _event(dict(existing))
            sequence = db.execute(
                'SELECT COALESCE(MAX(sequence), 0) + 1 FROM conversation_events '
                'WHERE conversation_id = ?', (conversation_id,)).fetchone()[0]
            db.execute('''INSERT INTO conversation_events
                (conversation_id,sequence,event_id,kind,payload_json,created_at)
                VALUES (?,?,?,?,?,?)''',
                       (conversation_id, sequence, event_id, kind, encoded, created_at))
            db.execute('UPDATE conversations SET updated_at = ?, revision = revision + 1 '
                       'WHERE id = ?',
                       (max(conversation['updated_at'], created_at), conversation_id))
            self._index(db, conversation_id, sequence, kind, payload)
            return _event({'conversation_id': conversation_id, 'sequence': sequence,
                           'event_id': event_id, 'kind': kind,
                           'payload_json': encoded, 'created_at': created_at})

        return self._write('append', work)

    def _fts_available(self, db: sqlite3.Connection) -> bool:
        if self._fts is None:
            try:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS conversation_events_fts "
                           "USING fts5(conversation_id UNINDEXED, sequence UNINDEXED, text, "
                           "kind UNINDEXED, tokenize='unicode61')")
                self._fts = True
            except sqlite3.DatabaseError:
                self._fts = False
        return self._fts

    def _index(self, db: sqlite3.Connection, conversation_id: str, sequence: int,
               kind: str, payload: Mapping[str, Any]) -> None:
        text = _searchable(kind, payload)
        if text is None or kind == 'assistant_delta':
            return
        if self._fts_available(db):
            db.execute('INSERT INTO conversation_events_fts '
                       '(conversation_id,sequence,text,kind) VALUES (?,?,?,?)',
                       (conversation_id, sequence, text, kind))

    def search(self, *, query: str, project_id: str | None = None,
               cursor: str | None = None, limit: int = 50) -> Mapping[str, Any]:
        if not isinstance(query, str):
            raise Invalid('query must be a string')
        query = query.strip()
        if not 1 <= len(query) <= MAX_QUERY:
            raise Invalid(f'query must be 1-{MAX_QUERY} characters')
        if project_id is not None:
            project_id = _check_id(project_id, 'project_id')
        limit = _check_int(limit, 'limit', 1)
        if limit > 100:
            raise Invalid('limit must be 1-100')
        scope = {'query': query, 'project_id': project_id}
        if cursor is not None:
            position = _decode_cursor(cursor)
            if {key: position.get(key) for key in scope} != scope:
                raise Conflict('Cursor does not belong to this query')
        else:
            position = None

        def work(db):
            project_clause = ''
            project_args: list[Any] = []
            if project_id is not None:
                project_clause = ' AND c.project_id = ?'
                project_args.append(project_id)
            if self._fts_available(db):
                match = _fts_query(query)
                rows = db.execute(
                    'SELECT e.conversation_id, e.sequence, e.event_id, e.kind, e.payload_json '
                    'FROM conversation_events_fts f JOIN conversation_events e '
                    'ON e.conversation_id = f.conversation_id AND e.sequence = f.sequence '
                    'JOIN conversations c ON c.id = e.conversation_id '
                    'WHERE conversation_events_fts MATCH ?' + project_clause +
                    ' ORDER BY e.conversation_id, e.sequence LIMIT 4000',
                    (match, *project_args)).fetchall()
            else:
                escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                rows = db.execute(
                    "SELECT e.conversation_id, e.sequence, e.event_id, e.kind, e.payload_json "
                    "FROM conversation_events e JOIN conversations c ON c.id = e.conversation_id "
                    "WHERE e.payload_json LIKE ? ESCAPE '\\'" + project_clause +
                    " AND e.kind != 'assistant_delta' ORDER BY e.conversation_id, e.sequence LIMIT 4000",
                    (f'%{escaped}%', *project_args)).fetchall()
            return [dict(row) for row in rows]

        rows = self._read(work)
        matches = []
        for row in rows:
            try:
                payload = json.loads(row['payload_json'])
            except ValueError:
                continue
            text = _searchable(row['kind'], payload)
            if not text:
                continue
            matches.append({'conversation_id': row['conversation_id'],
                            'sequence': row['sequence'],
                            'snippet': _snippet(text, query)})
        matches.sort(key=lambda item: (item['conversation_id'], item['sequence']))
        if position is not None:
            last = (position.get('conversation_id'), position.get('sequence'))
            matches = [item for item in matches
                       if (item['conversation_id'], item['sequence']) > last]
        page = matches[:limit]
        next_cursor = None
        if len(matches) > limit:
            next_cursor = _encode_cursor({**scope, 'conversation_id': page[-1]['conversation_id'],
                                          'sequence': page[-1]['sequence']})
        return {'matches': page, 'next_cursor': next_cursor}

    def patch(self, conversation_id: str, *, expected_revision: int,
              title: str | None = None, pinned: bool | None = None,
              archived: bool | None = None) -> Mapping[str, Any]:
        _check_id(conversation_id)
        expected_revision = _check_int(expected_revision, 'expected_revision', 0)
        if title is not None:
            title = _check_text(title, 'title', MAX_TITLE)
        if pinned is not None:
            pinned = _check_bool(pinned, 'pinned')
        if archived is not None:
            archived = _check_bool(archived, 'archived')
        assignments, args = [], []
        for column, value in (('title', title), ('pinned', pinned), ('archived', archived)):
            if value is not None:
                assignments.append(f'{column} = ?')
                args.append(int(value) if isinstance(value, bool) else value)
        now = utc_now()
        assignments.append('updated_at = ?')
        args.append(now)

        def work(db):
            db.execute('SELECT id FROM conversations WHERE id = ?', (conversation_id,)).fetchone()
            cursor = db.execute('UPDATE conversations SET ' + ', '.join(assignments) +
                                ', revision = revision + 1 WHERE id = ? AND revision = ?',
                                (*args, conversation_id, expected_revision))
            if cursor.rowcount != 1:
                raise Conflict('Conversation changed; reload before saving')
            return self._conversation(db, conversation_id)

        return self._write('patch', work)

    def export(self, conversation_id: str) -> Mapping[str, Any]:
        _check_id(conversation_id)

        def work(db):
            conversation = self._conversation(db, conversation_id)
            events = [dict(row) for row in db.execute(
                'SELECT * FROM conversation_events WHERE conversation_id = ? '
                "AND kind != 'assistant_delta' ORDER BY sequence", (conversation_id,))]
            return conversation, events

        conversation, events = self._read(work)
        document = {'version': 1, 'conversation': conversation,
                    'events': [_event(row) for row in events]}
        if len(json.dumps(document).encode()) > MAX_EXPORT:
            raise PayloadTooLarge('Export exceeds 2 MiB; page the transcript instead')
        return document

    def delete(self, conversation_id: str, *, expected_revision: int) -> None:
        _check_id(conversation_id)
        expected_revision = _check_int(expected_revision, 'expected_revision', 0)
        def work(db):
            row = db.execute('SELECT revision FROM conversations WHERE id = ?',
                             (conversation_id,)).fetchone()
            if row is None:
                raise NotFound('Conversation not found')
            if row[0] != expected_revision:
                raise Conflict('Conversation changed; reload before deleting')
            # Hold the write transaction across validation and invalidation so a
            # concurrent writer cannot change the reviewed revision in between.
            for callback in tuple(self._before_delete):
                try:
                    callback(conversation_id)
                except Exception as exc:
                    raise StorageError(
                        f'Deletion refused: derived data cleanup failed: {exc}') from exc
            cursor = db.execute('DELETE FROM conversations WHERE id = ? AND revision = ?',
                                (conversation_id, expected_revision))
            if cursor.rowcount != 1:
                exists = db.execute('SELECT 1 FROM conversations WHERE id = ?',
                                    (conversation_id,)).fetchone()
                if exists is None:
                    raise NotFound('Conversation not found')
                raise Conflict('Conversation changed; reload before deleting')
            if self._fts:
                db.execute('DELETE FROM conversation_events_fts WHERE conversation_id = ?',
                           (conversation_id,))

        self._write('delete', work)

    def register_before_delete(self, callback: Callable[[str], None]) -> None:
        """Invalidate only after revision validation, inside the write transaction.

        Callbacks must not open another write transaction on this database or
        call this store's write methods. Prefer extension foreign-key cascades
        for same-database rows. External cache invalidation must be idempotent:
        a later callback or commit failure can still refuse the deletion.
        """
        self._before_delete.append(callback)

    def prepare_resume(self, conversation_id: str) -> Mapping[str, Any]:
        _check_id(conversation_id)

        def work(db):
            conversation = self._conversation(db, conversation_id)
            events = [dict(row) for row in db.execute(
                'SELECT sequence,event_id,kind,payload_json,created_at '
                "FROM conversation_events WHERE conversation_id = ? AND kind != 'assistant_delta' "
                'ORDER BY sequence', (conversation_id,))]
            return conversation, events

        conversation, events = self._read(work)
        turns: list[dict[str, str]] = []
        pending_user: str | None = None
        unfinished: dict[str, dict[str, Any]] = {}
        for row in events:
            try:
                payload = json.loads(row['payload_json'])
            except ValueError:
                payload = {}
            text = payload.get('text') if isinstance(payload, dict) else None
            if row['kind'] == 'user':
                pending_user = text if isinstance(text, str) else ''
            elif row['kind'] == 'assistant':
                turns.append({'user': pending_user or '', 'assistant': text or ''})
                pending_user = None
            elif row['kind'] == 'tool_call':
                call_id = payload.get('call_id') if isinstance(payload, dict) else None
                key = call_id if isinstance(call_id, str) else f'sequence:{row["sequence"]}'
                unfinished[key] = {'sequence': row['sequence'], 'event_id': row['event_id'],
                                   'kind': 'tool_call'}
            elif row['kind'] == 'tool_result':
                call_id = payload.get('call_id') if isinstance(payload, dict) else None
                unfinished.pop(call_id if isinstance(call_id, str) else '', None)
            elif row['kind'] == 'interrupted':
                unfinished[f'interrupted:{row["sequence"]}'] = {
                    'sequence': row['sequence'], 'event_id': row['event_id'],
                    'kind': 'interrupted'}
        return {'conversation': conversation, 'turns': turns,
                'revision': conversation['revision'],
                'unfinished': list(unfinished.values()),
                'open_user': pending_user}

    def raw_events(self, conversation_id: str) -> tuple[Mapping[str, Any], ...]:
        """Every stored record including transient deltas, for lifecycle settlement."""
        rows = self._read(lambda db: [dict(row) for row in db.execute(
            'SELECT * FROM conversation_events WHERE conversation_id = ? ORDER BY sequence',
            (conversation_id,))])
        return tuple({'sequence': row['sequence'], 'event_id': row['event_id'],
                      'kind': row['kind'], 'created_at': row['created_at'],
                      'payload': json.loads(row['payload_json'])} for row in rows)

    def all_conversation_ids(self) -> tuple[str, ...]:
        """Every durable conversation, for restart-time interruption settlement."""
        rows = self._read(lambda db: [row[0] for row in db.execute(
            'SELECT id FROM conversations ORDER BY created_at, id')])
        return tuple(rows)


def _event(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = row.get('payload_json')
    return {'sequence': row['sequence'], 'event_id': row['event_id'], 'kind': row['kind'],
            'created_at': row['created_at'],
            'payload': json.loads(payload) if isinstance(payload, str) else row.get('payload')}


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return True
