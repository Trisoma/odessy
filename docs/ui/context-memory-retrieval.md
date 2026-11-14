# Context, memory and retrieval

Agent-basics Wave 2 (prompt 04) adds three local, offline backends beside the durable
conversation store. None of them calls a model, a network service or a subprocess; no
embeddings, vector database or model download is used. All data lives in the same
SQLite database as conversations, under `context_`, `memory_` and `retrieval_` tables
versioned in `schema_extensions`. The core schema pragma remains owned by the
conversation store.

## Prepared context (B04/B10)

For each model turn the shared owner can ask for a bounded `PreparedContext`:

1. a durable extractive summary of older completed exchanges (method
   `extractive-v1`, clearly labelled as a baseline);
2. explicit enabled memory facts for the conversation's project;
3. document excerpts retrieved for the current query, with citations;
4. the most recent completed exchanges verbatim.

Everything counts against one character budget (24,000 by default), including the
labels the shared wrapper adds. Older exchanges appear only through the summary;
recent ones are not duplicated. Only completed user/assistant text participates —
tool calls, tool results, approvals, grants, process handles and capture state are
never copied into context. A summary is stored with its conversation, revision,
`through_sequence` and source hash; it is reused while its range is unchanged and a
new version is written when older history grows. Raw history is always preserved.

`GET /v1/conversations/{cid}/context` returns the last context that was actually
prepared for a request (summary, memory ids, citations, characters used, budget and
method), not a preview and not an action. Opening a page never prepares context.

## Explicit memory (B11)

Facts are created only by the user, scoped to one project, and carry a revision,
timestamps and `source: user`. They can be edited, enabled/disabled or deleted; the
project route cannot see or change another project's facts. Disabled and deleted
facts immediately disappear from `enabled_facts`, so the next prepared request cannot
include them. At most 100 facts are injected, text is capped at 2,000 characters per
fact, and injection stops at the context budget (visible in `memory_ids`).

Chat history, application settings and long-term memory are separate stores: this
feature never writes preferences or credentials.

## Local document retrieval (B12)

Opt-in indexing accepts user-selected relative paths inside the live authorized
workspace, read through the existing inspector callbacks (hooks, sensitive-path
rules, symlinks/junctions, current grants). Supported formats: `.md`, `.markdown`,
`.txt`, `.py`, `.dart`, `.ts`, `.tsx`, `.js`, `.jsx`, `.go`, `.rs`, `.java`, `.kt`,
`.c`, `.h`, `.cpp`, `.hpp`, `.cs`, `.rb`, `.php`, `.sh`, `.ps1`, `.json`, `.yaml`,
`.yml`, `.toml`, `.ini`, `.sql`, `.html`, `.css`, `.scss`, `.xml`, `.rst`, `.cfg`.

Refused: absolute paths, `..`, control characters, drive letters, `.git`/`.ssh`/
`.aws`/`.gnupg`/`.venv`/`node_modules`, dot-env files, keys/certificates, credential
files, lockfiles, databases, binary content (NUL bytes) and unsupported extensions.
Limits: 256 KiB per file, 100 files and 10 MiB of text per project. Lines are chunked
at about 2,000 characters with 200 characters of overlap; every chunk keeps its
1-based start/end line and the file's source hash.

Search uses SQLite FTS5 (`bm25`) when available and the same service's deterministic
lexical ranking otherwise (term counts, then path and line). Citations contain the
document id, path, line range, source hash, excerpt and score. Results are project
scoped and never include chunks from stale, errored or deleted documents.

Documents can be refreshed idempotently (same path keeps its id, increments its
revision and re-chunks), removed with a revision check, and verified against the
live file hashes. A changed or missing source becomes `stale`/`error` and stays out
of retrieval until it is refreshed; removal deletes its chunks and search rows.
Deleting a chat never deletes indexed files or unrelated source files.

Retrieved text is untrusted data. It cannot grant authority, does not approve
anything and is never treated as system instructions. Retrieval injection is
supported only for the Custom engine; Harness has no continuation or context
injection and stays read-only history.
# Audit handoff: request-time source validation

The shared session adapter must await `context.validate_sources(cid)` immediately
before calling the synchronous, bounded `prepare`. The factory supplies the live
owner/workspace callbacks; validation rereads indexed sources through the existing
inspector, excludes drifted/denied sources, and rechecks owner identity. A direct
`prepare` call is data composition only and does not prove current filesystem access.
06's adapter supplies this barrier; 07 must mount the services and test it together.
