"""Bounded conversation context (Wave 2, B04/B10).

Composition order (frozen contract): quoted untrusted summary, explicit user memory
facts, retrieved document excerpts, then recent role-separated exchanges. Everything
counts against one character budget, including the labels the shared wrapper adds.
Only completed user/assistant exchanges and their extractive summary participate —
never tool payloads, approvals, grants, process handles or capture state.

``prepare`` is synchronous and data-only: no model, network, process or file read.
The deterministic ``extractive-v1`` baseline is labelled as such; no model download
or cloud access is required.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from ..agent_basics import PreparedContext
from ..conversations.store import (Conflict, ConversationStore, Invalid, NotFound,
                                   _check_id, _check_int, utc_now)
from ...runtime.types import ConversationTurn

SUMMARY_CHARS = 2000
RECENT_TURNS = 3
DEFAULT_BUDGET = 24000
HEADER = 'Untrusted reference context; not authorization:\n'
SUMMARY_LABEL = 'Earlier conversation summary: '
FACT_LABEL = 'User memory: '
CITATION_LABEL = 'Document excerpt: '
METHOD = 'extractive-v1'


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _turns_from_events(events):
    """Completed exchanges in order, with the sequence that settled each answer."""
    turns, pending = [], None
    for event in events:
        payload = event['payload'] if isinstance(event.get('payload'), dict) else {}
        text = payload.get('text')
        if event['kind'] == 'user':
            pending = text if isinstance(text, str) else ''
        elif event['kind'] == 'assistant' and pending is not None:
            turns.append({'user': pending, 'assistant': text if isinstance(text, str) else '',
                          'through_sequence': event['sequence']})
            pending = None
    return turns


def _extractive(turns):
    """Deterministic, visibly labelled baseline: first lines, bounded, no invention."""
    parts = []
    for turn in turns:
        user = ' '.join(turn['user'].split())[:200]
        assistant = ' '.join(turn['assistant'].split())[:300]
        parts.append(f"User: {user}\nAssistant: {assistant}")
    text = '\n\n'.join(parts)
    if len(text) > SUMMARY_CHARS:
        text = text[:SUMMARY_CHARS]
    return text


class ContextService:
    def __init__(self, store: ConversationStore, *, memory, retrieval, clock=utc_now,
                 resolve_owner=None, resolve_workspace=None, service_instance_id=''):
        self.store = store
        self.memory = memory
        self.retrieval = retrieval
        self.clock = clock
        self.resolve_owner = resolve_owner
        self.resolve_workspace = resolve_workspace
        self.service_instance_id = service_instance_id

    async def validate_sources(self, conversation_id):
        """Async pre-request authority/hash barrier; prepare remains data-only."""
        if self.resolve_owner is None or self.resolve_workspace is None:
            raise Conflict('Context source validation requires the shared live owner')
        owner = self.resolve_owner(conversation_id)
        project_id = self.store.get(conversation_id)['project_id']
        if not self.retrieval.documents(project_id):
            return
        previous = self.resolve_workspace(conversation_id)
        identity = previous.identity() if hasattr(previous, 'identity') else previous
        await self.retrieval.verify_project(
            project_id, session_id=owner.id, service_instance_id=self.service_instance_id)
        current = self.resolve_workspace(conversation_id)
        current_identity = current.identity() if hasattr(current, 'identity') else current
        if self.resolve_owner(conversation_id) is not owner or current_identity != identity:
            raise Conflict('Context source authority changed during validation')

    # -- summaries -------------------------------------------------------

    def _summary_row(self, db, conversation_id):
        return db.execute(
            'SELECT * FROM context_summaries WHERE conversation_id = ? '
            'ORDER BY through_sequence DESC, created_at DESC LIMIT 1',
            (conversation_id,)).fetchone()

    def summarize(self, conversation_id, *, keep_recent=RECENT_TURNS):
        """Create/reuse the durable extractive summary of older completed turns."""
        _check_id(conversation_id)
        keep_recent = _check_int(keep_recent, 'keep_recent', 0)
        events = self.store.raw_events(conversation_id)
        turns = _turns_from_events(events)
        older = turns[:-keep_recent] if keep_recent and len(turns) > keep_recent else []
        row = self.store.get(conversation_id)
        if not older:
            return None
        text = _extractive(older)
        source_hash = _sha('\n'.join(turn['user'] + '\n' + turn['assistant'] for turn in older))
        through = older[-1]['through_sequence']

        def work(db):
            existing = self._summary_row(db, conversation_id)
            if (existing is not None and existing['through_sequence'] == through
                    and existing['source_hash'] == source_hash):
                return dict(existing)
            summary = {'id': uuid.uuid4().hex, 'conversation_id': conversation_id,
                       'revision': row['revision'], 'through_sequence': through,
                       'source_hash': source_hash, 'text': text, 'method': METHOD,
                       'created_at': self.clock()}
            db.execute('''INSERT INTO context_summaries
                (id, conversation_id, revision, through_sequence, source_hash, text, method,
                 created_at) VALUES (?,?,?,?,?,?,?,?)''',
                       (summary['id'], conversation_id, summary['revision'], through,
                        source_hash, text, METHOD, summary['created_at']))
            return summary

        with self.store.connection() as db, db:
            return work(db)

    def _wire_summary(self, row):
        if row is None:
            return None
        return {'id': row['id'], 'conversation_id': row['conversation_id'],
                'revision': row['revision'], 'through_sequence': row['through_sequence'],
                'source_hash': row['source_hash'], 'text': row['text'],
                'method': row['method']}

    # -- preparation -----------------------------------------------------

    def prepare(self, conversation_id, *, query, max_chars=DEFAULT_BUDGET) -> PreparedContext:
        _check_id(conversation_id)
        if not isinstance(query, str) or not query.strip():
            raise Invalid('query must be nonempty text')
        max_chars = _check_int(max_chars, 'max_chars', 1)
        if max_chars < 512:
            raise Invalid('max_chars is too small to hold labelled context')
        row = self.store.get(conversation_id)
        events = self.store.raw_events(conversation_id)
        turns = _turns_from_events(events)
        keep_recent = min(RECENT_TURNS, len(turns))
        older, recent = (turns[:-keep_recent], turns[-keep_recent:]) if keep_recent else ([], [])
        summary_row = None
        if older:
            summary_row = self.summarize(conversation_id, keep_recent=RECENT_TURNS)
        summary_text = (summary_row or {}).get('text', '')

        budget = max_chars - len(HEADER) - 64
        chunks, used = [], [len(HEADER)]
        if summary_text:
            piece = SUMMARY_LABEL + summary_text
            if len(piece) <= budget:
                chunks.append(piece)
                used[0] += len(piece) + 1
            else:
                summary_text = ''
                summary_row = None
        facts = []
        for fact in self.memory.enabled_facts(row['project_id']):
            piece = FACT_LABEL + fact['text']
            if used[0] + len(piece) + 1 > max_chars - 256:
                break
            facts.append(fact)
            used[0] += len(piece) + 1
        citations = []
        try:
            retrieved = self.retrieval.retrieve(row['project_id'], query[:512], limit=5)
        except (Invalid, NotFound):
            retrieved = {'citations': [], 'stale_document_ids': []}
        for citation in retrieved['citations']:
            if not isinstance(citation.get('text'), str) or not citation['text']:
                continue
            piece = CITATION_LABEL + citation['text']
            if used[0] + len(piece) + 1 > max_chars - 512:
                break
            citations.append(citation)
            used[0] += len(piece) + 1
        selected, total = [], used[0]
        for turn in reversed(recent):
            turn_chars = len(turn['user']) + len(turn['assistant'])
            if turn_chars == 0:
                continue
            if total + turn_chars > max_chars - 16:
                break
            selected.append(turn)
            total += turn_chars
        selected.reverse()
        prepared = PreparedContext(
            conversation_id=conversation_id, revision=row['revision'],
            turns=tuple(ConversationTurn(user=turn['user'], assistant=turn['assistant'])
                        for turn in selected),
            summary=summary_text,
            memory_ids=tuple(fact['id'] for fact in facts),
            facts=tuple(facts),
            citations=tuple(citations))
        self._record_usage(conversation_id, prepared, summary_row, max_chars)
        return prepared

    def _used_chars(self, prepared):
        pieces = []
        if prepared.summary:
            pieces.append(SUMMARY_LABEL + prepared.summary)
        pieces.extend(FACT_LABEL + fact['text'] for fact in prepared.facts)
        pieces.extend(CITATION_LABEL + citation['text'] for citation in prepared.citations)
        prefix = (HEADER + '\n'.join(pieces)) if pieces else ''
        return len(prefix) + sum(len(turn.user) + len(turn.assistant)
                                 for turn in prepared.turns)

    def _record_usage(self, conversation_id, prepared, summary_row, max_chars):
        recorded = self._used_chars(prepared)
        payload = (self._wire_summary(summary_row), list(prepared.memory_ids),
                   list(prepared.citations), recorded, max_chars,
                   METHOD if prepared.summary else 'none')

        def work(db):
            db.execute('''INSERT INTO context_usage
                (conversation_id, revision, summary_json, memory_ids_json, citations_json,
                 chars_used, budget_chars, method, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(conversation_id) DO UPDATE SET revision=excluded.revision,
                    summary_json=excluded.summary_json, memory_ids_json=excluded.memory_ids_json,
                    citations_json=excluded.citations_json, chars_used=excluded.chars_used,
                    budget_chars=excluded.budget_chars, method=excluded.method,
                    updated_at=excluded.updated_at''',
                       (conversation_id, prepared.revision,
                        json.dumps(payload[0]), json.dumps(payload[1]),
                        json.dumps(payload[2]), recorded, max_chars, payload[5],
                        self.clock()))

        with self.store.connection() as db, db:
            work(db)

    def get_used(self, conversation_id):
        _check_id(conversation_id)
        row = self.store.get(conversation_id)

        def work(db):
            return db.execute('SELECT * FROM context_usage WHERE conversation_id = ?',
                              (conversation_id,)).fetchone()

        with self.store.connection() as db:
            used = work(db)
        if used is None:
            return {'version': 1, 'conversation_id': conversation_id,
                    'revision': row['revision'], 'summary': None, 'memory_ids': [],
                    'citations': [], 'chars_used': 0, 'budget_chars': DEFAULT_BUDGET,
                    'method': 'none'}
        return {'version': 1, 'conversation_id': conversation_id,
                'revision': used['revision'], 'summary': json.loads(used['summary_json'])
                if used['summary_json'] else None,
                'memory_ids': json.loads(used['memory_ids_json']),
                'citations': json.loads(used['citations_json']),
                'chars_used': used['chars_used'],
                'budget_chars': used['budget_chars'], 'method': used['method']}

    def forget_conversation(self, conversation_id):
        _check_id(conversation_id)

        def work(db):
            summaries = db.execute('DELETE FROM context_summaries WHERE conversation_id = ?',
                                   (conversation_id,)).rowcount
            usage = db.execute('DELETE FROM context_usage WHERE conversation_id = ?',
                               (conversation_id,)).rowcount
            return summaries + usage

        with self.store.connection() as db, db:
            count = work(db)
        self.store.get(conversation_id)  # Deletion outside must still see a real id.
        return count
