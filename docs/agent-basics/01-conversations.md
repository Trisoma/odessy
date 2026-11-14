# 01 — Durable conversations and history (Wave 1, slot A)

Start only after 00 READY. Own only assigned persistence/API modules/tests. Read COMMON.md and frozen contracts. Implement B01–B04 backend foundations and supply integration recipes for shared session lifecycle files.

Persist stable session/project/engine identities, titles/pin/archive state, ordered user/assistant messages, tool calls/results/activity, attachment metadata and interruption state. Store user data in the correct application profile, not the checkout. Use a versioned transactional schema and indexed/paginated queries. Streaming updates must not create duplicate messages after reconnect; settle partial/interrupted records truthfully. Define when durable writes happen relative to model/tool dispatch; disk failure must not falsely acknowledge a saved conversation. Record unknown outcomes without rerunning tools.

Provide create/list/read/rename/pin/archive/delete, message pagination, full-transcript search and versioned safe export. Deletion must invalidate owned history/search data and prepare cleanup hooks for Wave 2; do not delete arbitrary attached source files. Export is user-requested and excludes secrets. Preserve original timestamps and ordering. Persistent project references cannot remain tied only to a process-instance ID. Existing transient sessions need an explicit migration/adoption policy that does not lose live turns.

Separate history retrieval from safe same-engine continuation. Restore public content and allowed runtime state only; never restore approvals, live process handles or voice capture. State which engines genuinely continue after restart and why; history-only must not masquerade as resumable. Produce tests for real DB close/reopen/service recreation, multi-session isolation, migration, paging/search, archive/delete/export, partial streaming/reconnect and disk/corrupt DB errors. Use deterministic providers for request-context assertions, not only direct store unit tests.

Document durable context access and extension migration seams for 04. Write doc/reports/01.md with exact shared edits for 06, actual test results and B01–B04 remaining gates. Stop; do not edit 02/03 scopes or start 04.

## Hard time limit and isolation

Hard wall-clock limit: **150 minutes**, including inspection, code, tests and handoff. Reserve the last 10 minutes for checks/reporting. At the limit stop, report unfinished requirement IDs and recovery steps; never claim incomplete work finished. Read COMMON.md branch/worktree workflow. Use your assigned isolated worktree. Scoped local task commits are authorized; no push or develop merge.
