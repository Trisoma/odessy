"""Opt-in local document retrieval (Wave 2, B12).

Only user-selected relative paths inside the live authorized workspace are indexed.
Ingest reads through the trusted ``read_context_file`` callback (existing inspector
hooks, sensitive-path, symlink and authority checks), stores bounded line-aligned
chunks, and searches them with SQLite FTS5 when available, else a deterministic
lexical rank. Retrieved text is untrusted data: it carries citations and scores,
never grants, and nothing here executes or exports a file.
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from pathlib import PurePosixPath

from ..conversations.store import (Conflict, ConversationStore, Invalid, NotFound,
                                   PayloadTooLarge, StorageError, _check_id, _check_int,
                                   utc_now)

MAX_FILE_BYTES = 256 * 1024
MAX_FILES = 100
MAX_PROJECT_BYTES = 10 * 1024 * 1024
CHUNK_CHARS = 2000
OVERLAP_CHARS = 200
MAX_QUERY = 512
MAX_LIMIT = 10

ALLOWED_SUFFIXES = frozenset({
    '.md', '.markdown', '.txt', '.py', '.dart', '.ts', '.tsx', '.js', '.jsx',
    '.go', '.rs', '.java', '.kt', '.c', '.h', '.cpp', '.hpp', '.cs', '.rb',
    '.php', '.sh', '.ps1', '.json', '.yaml', '.yml', '.toml', '.ini', '.sql',
    '.html', '.css', '.scss', '.xml', '.rst', '.cfg',
})
SENSITIVE_NAMES = frozenset({
    '.env', '.npmrc', '.pypirc', '.netrc', '.gitconfig', '.git-credentials',
    'credentials', 'secrets', 'id_rsa', 'id_ed25519', 'known_hosts', 'odessy.env',
    'package-lock.json', 'pubspec.lock', 'poetry.lock',
})
SENSITIVE_SUFFIXES = ('.pem', '.key', '.pfx', '.p12', '.jks', '.sqlite3', '.db', '.env')
_SENSITIVE_DIRS = frozenset({'.git', '.ssh', '.aws', '.gnupg', '.dsh-meow', 'node_modules',
                             '__pycache__', '.venv', 'venv'})


def _validate_path(path, name='path'):
    if not isinstance(path, str) or not path or len(path) > 2048:
        raise Invalid('A relative workspace path is required')
    if any(ord(ch) < 32 for ch in path) or ':' in path:
        raise Invalid('A relative workspace path is required')
    normal = path.replace('\\', '/')
    if normal.startswith(('/', '~')):
        raise Invalid('A relative workspace path is required')
    parts = normal.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise Invalid('A relative workspace path is required')
    lowered = [part.casefold() for part in parts]
    if any(part in _SENSITIVE_DIRS for part in lowered):
        raise Invalid('Sensitive or generated paths are not indexed')
    file_name = lowered[-1]
    if (file_name in SENSITIVE_NAMES or file_name.endswith(SENSITIVE_SUFFIXES)
            or file_name.startswith('.env')):
        raise Invalid('Sensitive paths are not indexed')
    if PurePosixPath(normal).suffix.casefold() not in ALLOWED_SUFFIXES:
        raise Invalid('Only supported text, Markdown and code files are indexed')
    return normal


def _text_of(value):
    if isinstance(value, dict):
        return value.get('text')
    return getattr(value, 'text', None)


def _hash_of(value):
    if isinstance(value, dict):
        return value.get('sha256')
    return getattr(value, 'sha256', None)


def _chunks(text):
    lines = text.splitlines(keepends=True)
    offsets, position = [], 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    starts = [0]
    for index, line in enumerate(lines):
        starts.append(offsets[index] + len(line))

    def line_at(offset):
        # 1-based line containing ``offset``; end offsets map to their line.
        low, high = 0, len(offsets) - 1
        while low < high:
            middle = (low + high + 1) // 2
            if offsets[middle] <= offset:
                low = middle
            else:
                high = middle - 1
        return low + 1

    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        body = text[start:end]
        if not body:
            break
        chunks.append({'seq': len(chunks), 'start_line': line_at(start),
                       'end_line': line_at(max(start, end - 1)),
                       'chars': len(body), 'text': body})
        if end >= len(text):
            break
        start = end - OVERLAP_CHARS
    return chunks


def _terms(query):
    return [term.casefold() for term in re.split(r'\s+', query) if term][:16]


def _fts_match(query):
    quoted = ['"' + term.replace('"', '""') + '"' for term in _terms(query)]
    return ' AND '.join(quoted) if quoted else ''


class RetrievalService:
    """Async ingest/verify (file reads) plus synchronous bounded retrieval."""

    def __init__(self, store: ConversationStore, *, project_workspace, read_file,
                 session_conversation=None, clock=utc_now):
        self.store = store
        self.project_workspace = project_workspace
        self.read_file = read_file
        self.session_conversation = session_conversation
        self.clock = clock
        self._fts = None

    # -- helpers ---------------------------------------------------------

    def _conversation(self, session_id):
        if self.session_conversation is None:
            raise Conflict('Retrieval requires the shared session owner')
        conversation_id = self.session_conversation(session_id)
        _check_id(conversation_id)
        return conversation_id

    def _authorize(self, project_id, session_id, service_instance_id=None):
        _check_id(project_id, 'project_id')
        if not isinstance(session_id, str) or not session_id:
            raise Invalid('session_id is required')
        binding = self.project_workspace(project_id, session_id, service_instance_id)
        conversation_id = self._conversation(session_id)
        return binding, conversation_id

    def _project_exists(self, db, project_id):
        _check_id(project_id, 'project_id')
        if db.execute('SELECT 1 FROM projects WHERE id = ?', (project_id,)).fetchone() is None:
            raise NotFound('Project not found')

    def _wire(self, row):
        return {'version': 1, 'id': row['id'], 'project_id': row['project_id'],
                'path': row['path'], 'revision': row['revision'],
                'source_hash': row['source_hash'], 'status': row['status'],
                'chunks': row['chunks'], 'error': row['error']}

    def _document(self, db, project_id, document_id):
        _check_id(document_id, 'document_id')
        row = db.execute('SELECT * FROM retrieval_documents WHERE id = ?',
                         (document_id,)).fetchone()
        if row is None or row['project_id'] != project_id:
            raise NotFound('Document not found')
        return dict(row)

    def _fts_available(self, db):
        if self._fts is None:
            try:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_chunks_fts "
                           "USING fts5(chunk_id UNINDEXED, project_id UNINDEXED, "
                           "document_id UNINDEXED, text, tokenize='unicode61')")
                self._fts = True
            except sqlite3.DatabaseError:
                self._fts = False
        return self._fts

    def _drop_index(self, db, document_id):
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='retrieval_chunks_fts'").fetchone():
            db.execute('DELETE FROM retrieval_chunks_fts WHERE document_id = ?',
                       (document_id,))

    # -- documents -------------------------------------------------------

    def documents(self, project_id):
        with self.store.connection() as db:
            self._project_exists(db, project_id)
            return [self._wire(row) for row in db.execute(
                'SELECT * FROM retrieval_documents WHERE project_id = ? '
                'ORDER BY path, id', (project_id,))]

    async def ingest(self, project_id, *, session_id, service_instance_id, path):
        path = _validate_path(path)
        await_binding = self.project_workspace
        binding = await_binding(project_id, session_id, service_instance_id)
        conversation_id = self._conversation(session_id)
        value = await self.read_file(conversation_id, path)
        current, current_cid = self._authorize(project_id, session_id, service_instance_id)
        identity = lambda value: value.identity() if hasattr(value, 'identity') else value
        if current_cid != conversation_id or identity(current) != identity(binding):
            raise Conflict('Indexing authority changed during the read')
        text = _text_of(value)
        source_hash = _hash_of(value)
        if not isinstance(text, str) or not isinstance(source_hash, str):
            raise StorageError('The workspace returned an unreadable document')
        size = len(text.encode('utf-8'))
        if size > MAX_FILE_BYTES:
            raise PayloadTooLarge('Document exceeds 256 KiB')
        if '\x00' in text:
            raise Invalid('Binary files are not indexed')
        now = self.clock()
        chunks = _chunks(text)

        def work(db):
            self._project_exists(db, project_id)
            existing = db.execute(
                'SELECT * FROM retrieval_documents WHERE project_id = ? AND path = ?',
                (project_id, path)).fetchone()
            count = db.execute('SELECT COUNT(*) FROM retrieval_documents WHERE project_id = ?',
                               (project_id,)).fetchone()[0]
            if (existing is None or existing['status'] == 'error') and count >= MAX_FILES:
                raise Conflict('This project already has the maximum of 100 indexed documents')
            total = db.execute('SELECT COALESCE(SUM(size),0) FROM retrieval_documents '
                               'WHERE project_id = ? AND id != ?',
                               (project_id, existing['id'] if existing else '')).fetchone()[0]
            if total + size > MAX_PROJECT_BYTES:
                raise PayloadTooLarge('Indexing this file would exceed the 10 MiB project limit')
            document_id = existing['id'] if existing else uuid.uuid4().hex
            revision = (existing['revision'] + 1) if existing else 0
            if existing:
                self._drop_index(db, document_id)
                db.execute('DELETE FROM retrieval_chunks WHERE document_id = ?', (document_id,))
            db.execute('''INSERT INTO retrieval_documents
                (id, project_id, path, revision, source_hash, status, chunks, error, size,
                 verified_at, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id, path) DO UPDATE SET revision=excluded.revision,
                    source_hash=excluded.source_hash, status='ready', chunks=excluded.chunks,
                    error=NULL, size=excluded.size, verified_at=excluded.verified_at,
                    updated_at=excluded.updated_at''',
                       (document_id, project_id, path, revision, source_hash, 'ready',
                        len(chunks), None, size, now,
                        existing['created_at'] if existing else now, now))
            self._fts_available(db)
            for chunk in chunks:
                cursor = db.execute(
                    'INSERT INTO retrieval_chunks '
                    '(document_id, seq, start_line, end_line, chars, source_hash, text) '
                    'VALUES (?,?,?,?,?,?,?)',
                    (document_id, chunk['seq'], chunk['start_line'], chunk['end_line'],
                     chunk['chars'], source_hash, chunk['text']))
                if self._fts:
                    db.execute('INSERT INTO retrieval_chunks_fts '
                               '(chunk_id, project_id, document_id, text) VALUES (?,?,?,?)',
                               (cursor.lastrowid, project_id, document_id, chunk['text']))
            return self._wire(db.execute('SELECT * FROM retrieval_documents WHERE id = ?',
                                         (document_id,)).fetchone())

        with self.store.connection() as db, db:
            return work(db)

    async def refresh(self, project_id, *, session_id, service_instance_id, path):
        return await self.ingest(project_id, session_id=session_id,
                                 service_instance_id=service_instance_id, path=path)

    async def verify_project(self, project_id, *, session_id, service_instance_id):
        """Recheck every indexed file hash; drifted documents become stale.

        Chunks of a stale document are never returned until a refresh re-reads and
        re-indexes it. Denied/missing files become ``error`` with a public reason.
        """
        _, conversation_id = self._authorize(project_id, session_id, service_instance_id)
        listed = self.documents(project_id)
        verified = 0
        for document in listed:
            try:
                value = await self.read_file(conversation_id, document['path'])
                text = _text_of(value)
                if not isinstance(text, str) or _hash_of(value) != document['source_hash']:
                    self._mark(project_id, document['id'], 'stale', 'Source changed; refresh required')
                else:
                    self._mark(project_id, document['id'], 'ready', None, verified=True)
            except Exception as exc:  # denied, missing or unreadable after revocation
                self._mark(project_id, document['id'], 'error', type(exc).__name__)
            verified += 1
        return {'verified': verified, 'documents': self.documents(project_id)}

    def _mark(self, project_id, document_id, status, error, *, verified=False):
        now = self.clock()

        def work(db):
            self._document(db, project_id, document_id)
            db.execute('UPDATE retrieval_documents SET status = ?, error = ?, verified_at = ?, '
                       'updated_at = ? WHERE id = ?',
                       (status, error, now if verified else None, now, document_id))

        with self.store.connection() as db, db:
            work(db)

    def delete(self, project_id, document_id, *, expected_revision):
        expected_revision = _check_int(expected_revision, 'expected_revision')

        def work(db):
            self._project_exists(db, project_id)
            row = self._document(db, project_id, document_id)
            if row['revision'] != expected_revision:
                raise Conflict('Document changed; reload before removing it')
            self._drop_index(db, document_id)
            db.execute('DELETE FROM retrieval_documents WHERE id = ?', (document_id,))

        with self.store.connection() as db, db:
            work(db)

    # -- retrieval -------------------------------------------------------

    def retrieve(self, project_id, query, *, limit=5):
        if not isinstance(query, str):
            raise Invalid('query must be a string')
        query = query.strip()
        if not 1 <= len(query) <= MAX_QUERY:
            raise Invalid(f'query must be 1-{MAX_QUERY} characters')
        limit = _check_int(limit, 'limit', 1)
        if limit > MAX_LIMIT:
            raise Invalid(f'limit must be 1-{MAX_LIMIT}')

        def work(db):
            self._project_exists(db, project_id)
            stale = [row['id'] for row in db.execute(
                "SELECT id FROM retrieval_documents WHERE project_id = ? AND status != 'ready'",
                (project_id,))]
            if self._fts_available(db):
                match = _fts_match(query)
                rows = db.execute(
                    'SELECT c.*, d.path AS path, d.source_hash AS document_hash, '
                    'bm25(retrieval_chunks_fts) AS rank '
                    'FROM retrieval_chunks_fts '
                    'JOIN retrieval_chunks c ON c.id = retrieval_chunks_fts.chunk_id '
                    'JOIN retrieval_documents d ON d.id = c.document_id '
                    "WHERE retrieval_chunks_fts MATCH ? AND d.status = 'ready' "
                    'AND d.project_id = ? ORDER BY rank, d.path, c.start_line LIMIT ?',
                    (match, project_id, limit * 4)).fetchall()
                found = [(row, -float(row['rank'])) for row in rows]
            else:
                rows = db.execute(
                    'SELECT c.*, d.path AS path, d.source_hash AS document_hash '
                    'FROM retrieval_chunks c JOIN retrieval_documents d ON d.id = c.document_id '
                    "WHERE d.project_id = ? AND d.status = 'ready' LIMIT 2000",
                    (project_id,)).fetchall()
                terms = _terms(query)
                found = []
                for row in rows:
                    lowered = row['text'].casefold()
                    hits = sum(lowered.count(term) for term in terms)
                    if hits:
                        found.append((row, float(hits)))
                found.sort(key=lambda item: (-item[1], item[0]['path'], item[0]['start_line']))
                found = found[:limit * 4]
            citations = []
            for row, score in found[:limit]:
                citations.append({'document_id': row['document_id'], 'path': row['path'],
                                  'start_line': row['start_line'], 'end_line': row['end_line'],
                                  'source_hash': row['source_hash'], 'text': row['text'],
                                  'score': round(score, 4)})
            return {'citations': citations, 'stale_document_ids': stale}

        with self.store.connection() as db:
            return work(db)

    def forget_conversation(self, conversation_id):
        """Indexed documents are project-scoped; a chat deletion removes no file."""
        _check_id(conversation_id)
        return 0
