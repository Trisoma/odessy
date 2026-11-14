# Required scope and acceptance map

Every ID must be PASS / FAIL / BLOCKED / NOT RUN with supporting evidence in final closeout. PARTIAL details must name missing behaviors. Inclusion is not a claim that all can fit one day.

| ID | Required behavior | Main owner |
| --- | --- | --- |
| B01 | Durable sessions/messages/tool and activity records/attachment metadata; survive backend restart | 01 |
| B02 | Paginated transcript browsing, stable IDs, title/rename/pin/archive/delete, project association | 01 + 06 |
| B03 | Full transcript search; versioned export and deletion including associated owned indexes; secrets excluded | 01 + 06 |
| B04 | Restore context and continue supported same-engine sessions without replaying actions; interrupted/unknown outcomes visible | 01 + 04 + 07 |
| B05 | Ask every time / Approve for me / Full control, real backend enforcement and revocation | 02 + 06 |
| B06 | Outer icon rail for Chat, Activity & usage, Devices, Jobs, Handoffs; Settings at bottom; keyboard names/tooltips | 03 |
| B07 | Independent project/chat sidebar: New chat, search, nested projects/chats, Recent, collapse | 03 + 06 |
| B08 | Top-right inspector toggle, resizable panel, Files/Terminal/Changes, narrow overlay and draft retention | 03 + 05 + 06 |
| B09 | Compact composer; voice adjacent to model, advanced voice popover; functional permissions picker | 03 + 06 |
| B10 | Bounded context budgeting and durable versioned summaries with source-message ranges | 04 |
| B11 | Explicit project-scoped long-term memory: create/list/edit/delete/disable; inspect injected facts | 04 + 06 |
| B12 | Opt-in local document RAG: ingest allowed text/Markdown/code, chunk, lexical rank, grounded citations, refresh/remove | 04 + 06 |
| B13 | Real local interactive PTY: input/output, resize, interrupt/close, bounded scrollback, process ownership | 05 + 06 |
| B14 | Workspace file checkpoints, previewed/confirmed restore, pre-restore safety checkpoint, preserve unrelated data | 05 + 06 |
| B15 | Preserve existing functionality/settings/credentials/grants and both engines; no silent fallback | all |
| B16 | Updated package inventory/migrations/resources; isolated restart/install/upgrade qualification with data retention | 07 |
| B17 | Current production renders at wide/narrow/light/dark, startup identity, stale demo confusion resolved | 03 + 07 |

RAG means retrieved document chunks actually reach the model request with citations; a search page alone does not pass B12. Lexical retrieval is a valid baseline; semantic embeddings/vector DB are deferred, not falsely advertised. Memory means explicit user-controlled facts actually participate in bounded context; preferences stored only in a UI do not pass B11. Existing tool-result shortening is preserved but does not itself pass B10.

Visual reference: the user's Codex screenshot has an approximately 52px outer rail, approximately 300px project/history sidebar, broad central conversation, bottom composer and an upper-right panel toggle. Suggested Flutter defaults: 52–56px rail, 260–300px sidebar, 320–480px inspector; these are implementation choices, not hard pixel requirements. Collapse/overlay panels before crushing the conversation. Never copy the screenshot's Chinese labels: English only. Reduce duplicated headers/status rows; retain meaningful connection/running-model and approval scope information. Inspector starts closed unless user preference says otherwise, and auto-opening a diff must be reversible. Do not turn fixture output into a terminal.

Canonical current screenshots: C:/Users/13647/OneDrive/Desktop/MiMundo/GitHub/odessy/odessy/docs/view. Preserve them as starting evidence; write new renders under a new subdirectory with hashes/fixture descriptions. Never use the standalone tastecode_demo entry point for acceptance.
