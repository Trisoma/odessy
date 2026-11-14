# 04 — Bounded context, explicit memory and basic RAG (Wave 2, slot A)

Start only after 06 publishes WAVE2 READY. Read 01/02/03 receipts, frozen revision and COMMON.md. Implement B04/B10–B12 in owned backend modules/tests; 06 owns UI/client files and 07 final lifecycle wiring. Consume 01's store interfaces without editing its migrations or duplicating sessions.

Context: retain recent exchanges and generate durable versioned summaries for older ranges using available configured model adapters or a clearly labeled deterministic extractive baseline. Keep source message ranges/revisions and budget accounting; exclude secrets and authority tokens. Preserve raw history. Do not call existing tool-result truncation a conversation summary. Handle edits/deletion/failed summarization and context exhaustion explicitly. Verify restored conversation requests contain relevant earlier facts and never historical tool execution instructions/approval state. Use supported engine hooks; never fabricate Harness continuation.

Memory: explicit user-created project-scoped facts/preferences with CRUD, provenance, enable/disable, per-project isolation and deletion. No automatic extraction required. Compose bounded enabled facts into actual model requests; expose what was used. Don't confuse chat storage, settings preferences and long-term memory. Disabled/deleted memory must not remain in indexes, summaries or subsequent request caches.

RAG: opt-in indexing of user-selected allowed local text/Markdown/code inside authorized workspace roots. Implement bounded chunking, SQLite FTS if available (tested fallback lexical rank otherwise), top-k retrieval, file/line/source-revision citations and actual request injection. No embeddings service, vector server or model download. Enforce sensitive-path/size/binary/symlink/junction rules at ingestion AND retrieval; check access revocation, source drift/deletion and stale citations. Refresh/remove indexes predictably and expose empty/indexing/stale/denied/failure states. Retrieved content is untrusted data, never grants or system instructions.

Tests: cross-project isolation, revoke/delete/disable invalidation, deterministic ranking/citation source mapping, restart persistence, query budgets, escape/sensitive path refusal and actual prepared model requests. Supply 07 exact hook/mount changes; coordinate DTO additions through 06 without concurrent shared edits. Write doc/reports/04.md with supported formats, limits, context policy and test receipts.

## Hard time limit and isolation

Hard wall-clock limit: **150 minutes**, including inspection, code, tests and handoff. Reserve the last 10 minutes for checks/reporting. At the limit stop, report unfinished requirement IDs and recovery steps; never claim incomplete work finished. Read COMMON.md branch/worktree workflow. Use your assigned isolated worktree. Scoped local task commits are authorized; no push or develop merge.
