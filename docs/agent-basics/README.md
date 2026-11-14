# Odessy agent basics — eight progressive/parallel prompts

Created November 14, 2026 (Asia/Shanghai). This is an implementation pack, not a completion or release claim. It includes the user's pasted missing-feature audit and Codex-inspired layout requirements. No implementation is started by authoring this pack.

Repository: `../../odessy` from this folder, i.e. `C:/Users/13647/OneDrive/Desktop/MiMundo/GitHub/odessy/odessy`.

## Run order for three agents

| Phase | Slot A | Slot B | Slot C | Start gate |
| --- | --- | --- | --- | --- |
| Foundation | [00](00-foundation.md) | idle | idle | Start here |
| Wave 1 | [01: durable conversations](01-conversations.md) | [02: access modes](02-access-modes.md) | [03: production layout](03-layout.md) | 00 READY receipt |
| Wave 2 | [04: context, memory, retrieval](04-context-memory-retrieval.md) | [05: terminal and checkpoints](05-terminal-checkpoints.md) | [06: client and UX integration](06-client-integration.md) | All Wave 1 workers stop; integrator preflight in 06 declares WAVE2 READY |
| Closeout | [07](07-closeout.md) | idle | idle | All Wave 2 workers stop |

Exactly **8 numbered execution prompts**, maximum **3 simultaneous agents**. Give each agent its numbered file, COMMON.md and doc/contracts.md, doc/ownership.md, doc/requirements.md. Same-checkout operation uses exclusive file ownership; no concurrent Git mutations. Do not automatically execute another prompt. In Wave 2, start 06 first for its short preflight; it publishes the gate before 04/05 start. Workers may then proceed against frozen interfaces without waiting for each other's implementation. 07 performs final real wiring and corrections after all writers stop.

Use capable coding agents; no model/provider is mandated. Strongest integrator takes 00, 06 and 07 sequentially. Context-heavy work goes to 01/04; policy/execution work to 02/05; Flutter work to 03/06.

## Honest one-day target

Plan approximately 45 minutes foundation, 2–3 hours Wave 1, 2–3 hours Wave 2, and 1–2 hours closeout. These are planning allocations, not measured estimates or per-agent hard timeouts. Stop and report when budget is exhausted; never silently reduce a required feature to a fixture, stub or smoke test. The full scope is ambitious for one day. If there is a cut, explicitly report unfinished requirement IDs and withhold the affected completion claim.

Core beta gates: durable history/continuation, working backend access modes, production layout, packaging/data preservation. Also included: editable explicit memory, lexical document RAG, interactive local terminal, reversible file checkpoints. Do not quietly omit the latter just to say beta-ready. No vector database/server or model download is required for the bounded retrieval design.

## Authoritative visual inputs

Current rendered screenshots: `../../odessy/docs/view/` (user-confirmed), especially its README and manifest. User's attached Codex screenshot defines the requested arrangement: outer icon rail, project/chat sidebar, top-right inspector toggle, voice next to model. See doc/requirements.md for a textual specification that survives a new chat. Existing screenshots describe the starting point, not the desired final layout.

**Potential stale demo:** do not evaluate `lib/tastecode_demo.dart` or the standalone `features/ui_demo/`. Check the actual production entry point, executable hash/build time and launch arguments. Production `--demo` is an explicit limited fixture mode, not proof of connected operation. Compare rebuilt production renders and connected service behavior, not a stale executable or a mismatched screenshot fixture.

References, read-only: `../../deepseek-harness-master`, `../../tastecode/tastecode-dev`. Reuse supported dependencies/adapters before porting source. Their presence on disk does not make them shipped Odessy features.

Reports stay in this pack's doc/reports/. Production documentation/migrations/resources belong in the app repository. Preserve old restructure packs.

## Updated execution rules

Use isolated task branches/worktrees and sequential local cherry-picks as specified in COMMON.md; this supersedes the earlier same-checkout description. No worktrees are created while authoring this pack.

Hard limits (minutes): 00=45, 01=150, 02=120, 03=120, 04=150, 05=150, 06=150 including preflight, 07=120. Limits include tests/reporting. Stop and report unfinished work at the limit. The critical path can approach 7.75 hours before unexpected delays; same-day completion is not guaranteed. Local scoped commits are authorized; push/develop merge/cleanup are not.

The user plans to merge the UI branch today. That is compatible with this pack: branch the new integration work from its verified tip, freeze worker bases, and reconcile develop sequentially after workers finish. If already merged, start from the verified develop result containing the UI changes. See COMMON.md base-branch clarification; no active-worker rebase or automatic merge to develop.

Prompt00 completed: **READY for Wave1**, frozen base61438b14064513f5a6c0055bc370c5ba376a67d7, three worker worktrees already created. Read [00 receipt](doc/reports/00.md) before starting01–03; don't rerun00 or create duplicate worktrees. Full source/qualification still pending.
