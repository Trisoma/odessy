"""Wave 2 extension schema owned by prompt 04 (context, memory, retrieval).

All tables are prefixed ``context_``/``memory_``/``retrieval_`` and versioned in the
core ``schema_extensions`` table. The core ``PRAGMA user_version`` stays owned by
prompt 01 and is never touched here. Migrations are idempotent and run inside one
serialized transaction after core setup.
"""
from __future__ import annotations

import sqlite3

EXTENSION_VERSION = 1
TABLES = ('context', 'memory', 'retrieval')


def migrate_extensions(connection: sqlite3.Connection) -> None:
    """Create/upgrade this worker's extension tables exactly once per version.

    07 calls this after core store setup with the store's serialized connection.
    Re-running it against the same database is a no-op; a future version is not
    silently downgraded (the version row is only advanced here).
    """
    for name in TABLES:
        row = connection.execute(
            'SELECT version FROM schema_extensions WHERE name = ?', (name,)).fetchone()
        if row is not None and row[0] > EXTENSION_VERSION:
            raise sqlite3.OperationalError(
                f'{name} extension schema {row[0]} is newer than supported {EXTENSION_VERSION}')
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS context_summaries (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL,
            through_sequence INTEGER NOT NULL,
            source_hash TEXT NOT NULL,
            text TEXT NOT NULL,
            method TEXT NOT NULL,
            created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS context_summaries_latest
            ON context_summaries(conversation_id, through_sequence DESC);
        CREATE TABLE IF NOT EXISTS context_usage (
            conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL,
            summary_json TEXT,
            memory_ids_json TEXT NOT NULL,
            citations_json TEXT NOT NULL,
            chars_used INTEGER NOT NULL,
            budget_chars INTEGER NOT NULL,
            method TEXT NOT NULL,
            updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_facts (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            text TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            revision INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS memory_facts_project
            ON memory_facts(project_id, enabled, updated_at, id);
        CREATE TABLE IF NOT EXISTS retrieval_documents (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            path TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0,
            source_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            chunks INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            size INTEGER NOT NULL DEFAULT 0,
            verified_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(project_id, path));
        CREATE TABLE IF NOT EXISTS retrieval_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id TEXT NOT NULL REFERENCES retrieval_documents(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            chars INTEGER NOT NULL,
            source_hash TEXT NOT NULL,
            text TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS retrieval_chunks_document
            ON retrieval_chunks(document_id, seq);
    ''')
    for name in TABLES:
        connection.execute(
            'INSERT OR IGNORE INTO schema_extensions(name, version) VALUES (?, ?)',
            (name, EXTENSION_VERSION))
    connection.commit()


def foreign_keys_on(connection: sqlite3.Connection) -> bool:
    return bool(connection.execute('PRAGMA foreign_keys').fetchone()[0])
