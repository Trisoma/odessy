# Durable conversations and history

Odessy keeps a conversation's durable record next to the existing configuration, in
`conversations.sqlite3` beside the file named by `ODESSY_CONFIG_FILE`, or under the
current user's `.config/odessy` otherwise. The database is separate from
`usage-history.sqlite3`, monitoring/telemetry data and the application checkout. It
belongs to the service's profile; a connected client sees that service's history.

The first durable implementation (agent-basics Wave 1, prompt 01) stores projects,
conversations and their ordered records: user messages, settled assistant messages,
transient streaming deltas, tool calls/results, activity, attachment metadata and
interruption records. Each conversation has a server-generated UUID, a durable
project association, an immutable engine (`legacy` or `harness`), a profile name,
title, pin/archive state and a monotonically increasing revision. Titles are limited
to 120 characters, a record payload to 256 KiB and a conversation export to 2 MiB.
Records keep the timestamp the recorder supplied, so imported/adopted history keeps
its original ordering.

## Durability relative to dispatch

The recorder writes before the action it describes:

* the admitted user message is appended before the model starts; a failed append is
  raised to the caller and the turn must be refused, because nothing was saved;
* a `tool_call` is appended before the tool has an effect and a `tool_result` after
  it returns;
* settled assistant text is appended after the turn completes;
* streamed deltas are best-effort and transient: they are deduplicated by a
  deterministic event id so a reconnecting client cannot create duplicates, and they
  are not listed in message pages, not searched and not exported. The settled
  `assistant` record is the message.

If the service stops between a tool call and its result, the next start marks the
open tool call and any open assistant stream as `interrupted` with outcome `unknown`
and the partial text that was observed. Nothing is retried automatically and no tool
is re-run. Stop does not imply rollback: a recorded tool call still happened.

Writes are transactional, serialized and committed before success is reported. A
disk, busy or I/O failure returns a storage error and the operation is not
acknowledged as saved. A database from a newer schema version or a corrupt file is
rejected, never silently reset. The core schema version belongs to this module;
Wave 2 extension tables use `schema_extensions` and must not edit `PRAGMA
user_version`.

## History versus resumability

Reading history never creates a runtime. Listing, opening, searching, exporting,
renaming, pinning, archiving and deleting do not construct an engine or session;
they are authenticated HTTP reads/writes against the service's database.

`continuation` is reported per conversation:

* `continuable` — only a `legacy` conversation whose shared session owner has bound a
  continuation handler. Resuming restores the prior public exchanges into a fresh
  controller with the same durable conversation, a new live session, fresh grants and
  no pending approvals, jobs or audio. Concurrent resumes are serialized; a second
  one is refused rather than creating a second live controller.
* `history_only` — every Harness conversation (the current runtime uses
  `session/new`; there is no native load, and replaying the transcript into a new
  session is not restoration) and any legacy conversation whose continuation owner is
  absent. `POST /v1/conversations/{id}/resume` answers `501 unsupported`; the durable
  record stays browsable.

`prepare_resume` returns data only: the conversation row, completed
`{user, assistant}` turns, the revision it read, the open user message (if any) and
visible unfinished records. It never restores approvals, process handles, grants,
capture or tool identifiers.

## Transient sessions

A live session is never silently adopted or overwritten. `adopt_live_session` is the
explicit policy for keeping one: it copies only completed exchanges under event ids
derived from the live session id, so repeating it converges instead of duplicating
history. The running turn is untouched and becomes durable when it settles through
the recorder. Conversations that were never adopted remain transient and disappear
on restart; they were never claimed as saved.

## Extension seams for Wave 2

* `ConversationStore.connection()` — serialized extension migration/access after core
  setup; 04 owns `context_*`, `memory_*`, `retrieval_*` tables and versions them in
  `schema_extensions`.
* `ConversationService.register_before_delete(callback)` — derived summaries,
  search/context-use data and citations are invalidated before a conversation's rows
  are deleted; a callback that fails refuses the deletion instead of leaving
  orphaned derived data. Source documents attached to the conversation are never
  deleted by this hook.
* `prepare_resume`/`messages`/`raw_events` — the bounded read paths 04 uses to build
  context; context is untrusted data, never authority, and page-opens must not
  prepare context or call a model.
* `ConversationRecorder` — the only write path 06 wires into session/agent-loop
  lifecycle; 04 must not append core events directly.
