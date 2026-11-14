# Continue Odessy 06 preflight — integration unfinished

You are taking over an interrupted implementation. Read this handoff, then verify live state. Preserve all dirty work. Do not start over, reset, clean, push, merge develop, run historical prompt packs, or declare READY from reports alone.

## User objective and scope

Complete **06 preflight only**: integrate stopped 01–03 workers, apply precise shared registration/lifecycle edits, reconcile DTOs, verify backend and production Flutter shell together, settle continuation/context contracts for 04/05, and publish an explicit WAVE2 READY receipt with revision in doc/reports/06.md. Release 04/05 only when justified. Full 06 client UX is a later phase, potentially handled by DeepSeek in its own worktree. User requested a handoff now, so prior agent stopped without completing the gate.

Original 06 budget: 60 minutes, reserve last 10 for validation/reporting; started 2026-11-14 14:41:32 Asia/Shanghai. Do not silently treat the original budget as reset. State your available continuation budget and stop honestly if exhausted. Prior estimate was 30–35 additional minutes for preflight, not all Wave 2 work.

## Paths and workflow

Parent: C:/Users/13647/OneDrive/Desktop/MiMundo/GitHub/odessy

Active worktree: C:/Users/13647/OneDrive/Desktop/MiMundo/GitHub/odessy/.worktrees/agent-basics-integration

Branch: feat/agent-basics-integration; current HEAD verified at handoff: **22c8a49**. Many intentional uncommitted changes listed below. Scoped local commits are authorized; no push/develop merge.

Prompt/contracts pack (outside application Git): C:/Users/13647/OneDrive/Desktop/MiMundo/GitHub/odessy/restructure/agent-basics

Read COMMON.md, doc/contracts.md, doc/ownership.md, doc/requirements.md, doc/reports/00.md through 03.md, and wave1-handoff if present. Locate exact filenames with rg --files. Original app checkout is parent/odessy, branch feat/ui-wiring-integration; last observed 6160d53. Do not modify it or assume its dirty state. Frozen Wave 1 base was 61438b14064513f5a6c0055bc370c5ba376a67d7.

Wave 1 was clean/stopped before integration. Sequential cherry-picks completed without conflicts:

- 01 original 051acd15… -> 8474b0d
- 01 fix a402d79f70da8de562ab58c9cb910d5abd083755 -> 2f9e7bf
- 02 b9bffde -> bc9bae8
- 03 1237f5e5a8f26101cf0ef104a0da40a961ef0a43 -> b7e4fb3
- 03 7fece34dd9ba1fcc23a364ea55325a6424fd9357 -> 55a5be4
- 03 a872cf3 -> fec4656
- 03 manifest fix 74a962ffa870d3ad4cbd0b5edff404e843afd5f2 -> 22c8a49

## Implemented but UNCOMMITTED

Inspect actual diff before modifying. No final preflight commit exists.

1. New application/basics_integration.py: PersistentComposition mounts conversation/access-mode routes; config-local conversations.sqlite3, restart settlement, app.state service, capability route; attaches ConversationRecorder and mode service to controllers; creates durable conversation on live-session creation; restores saved Custom context on resume. Current resume invokes raw factory — important flaw below. context_provider is optional and disabled by default.
2. application/api.py: create_app(conversations_enabled=False) opt-in preserving embedded tests; composition lifecycle and DB close; snapshots include conversation_id; controller attach; composition.resume_allowed = admission_open. serve.py enables composition for normal service.
3. application/session.py: run_turn persistence wrapper, user admission before model call, interruption/activity; assistant settlement before public transcript/memory append; optional context_provider.prepare injects explicitly untrusted summary/facts/citations and saved turns for Custom; passes underlying agent a _basics_session; mode close on controller close. Tool/public streaming coverage remains incomplete.
4. runtime/agent_loop.py: mode review during single-call preparation, fallback broker for ask; policy/revalidation preserved; dispatch validation checks revision and consumes grants once; recorder tool_call before execution and tool_result status after. Batch and other approval paths require review.
5. conversations/store.py: uses contextlib.closing for initialization connection to avoid Windows dangling SQLite connection.
6. Flutter app_icon_rail.dart: requests/applies focus before navigation; app.dart: dedicated drawer navigation FocusNodes and disposal to retain focus on cancelled settings navigation.
7. New tests/test_agent_basics_integration.py: six integration cases for durable restart/resume/context, ask/auto/full real agent preparation/dispatch, stale revision rejection, Harness read-only/501, persistence fault before model call and context injection.
8. Seventeen desktop test files updated by frontend helper for actual icon-rail/navigation/voice-popover structure, device details selector, explicit settings fixture, focus and semantics. These are uncommitted and need verification; do not assume passing. Files: app_shell_layout, conversation_presentation, conversation_visual, desktop_application, desktop_visual, device_page, device_visual, gui_audit_regressions, monitoring_app_integration, navigation_layout, navigation_ux, presentation_accessibility, settings_shell_guard, settings_visual, source_setup_wiring, usage_dashboard, visual_preview (all *_test.dart).

## Blocking findings to resolve before READY

Read-only backend reviewer contracts_recon identified:

1. Resume bypasses make_controller lifecycle: raw factory misses pending_admissions, monitoring.attach and translated errors. Expose shared controller creation callback to composition and reuse it, with admission checks before/after awaited construction and safe failure close.
2. Historical workspace binding unresolved: old project A context can resume against currently configured workspace B. Persist trusted workspace identity/binding and reject mismatch before restoration or require explicit trusted selection. Never infer authority from project ID or restore stale grants. Add root A -> B rejection test. Existing/no-root histories need explicit conservative semantics.
3. Batch path asks via _ask_batch before per-call review, so auto/full may still prompt. Inspect and route supported calls through the same mode decision barrier; do not weaken policies or claim coverage you lack. Direct SessionController.confirm_action jobs/handoffs also bypass modes; freeze precise supported coverage, retain independent grants/remote escalation.
4. B01 persistence currently records user/settled assistant/generic activity and tool names/status, but not tool output, partial stream deltas, attachments/public-event metadata. Crash during stream loses observed partial text. Wire necessary public/redacted bounded payloads and deltas or explicitly block completion. Avoid storing secrets or arbitrary workspace contents. Do not put persistence failure outside cleanup-protected lifecycle.
5. 04 context prepare(cid,query) needs trusted live-controller/scope resolver. Freeze a resolver/provider wrapper seam so project ID cannot authorize indexing/RAG access and revocation is checked. Current provider hook alone is insufficient.
6. Reviewer flagged empty resume service_instance_id. This may be a false positive: register_conversations appears to fill it from dependencies. Verify source and add assertion rather than blindly rewriting.

Review useful implementation details: recorder has begin_turn/settle/interrupted/tool_call/tool_result/delta methods; inspect exact signatures. Store deletion fix already validates revision/existence inside BEGIN IMMEDIATE BEFORE owner hooks. Hooks inside transaction must not perform nested writes to same DB; prefer FK cascade and idempotent external-cache cleanup.

Full control must stay within current granted authority; hard deny, confinement, sensitive paths, remote escalation and credentials remain enforced. Harness continuation remains false/read-only with 501 until source-backed support is genuinely wired. No fake endpoints or automatic fixture fallbacks. Normal service must mount the features, not merely test-only routes.

## Verified tests and latest failures

Earlier combined Python suite: **181 passed** (16.30s) before later context/store-closing edits. Included integration/foundation/store/service/routes/access/API/session/bootstrap/stop/runtime/approval/workspace inspector suites. Latest new integration suite **6 passed**; later integration+session+stop **31 passed**. These are focused, not full repository/installed qualification.

Desktop pre-integration baseline: 527 passed, 13 skipped, 53 failed; many stale selectors but NOT all. First repaired full run left 23 failures. Latest focused run FINISHED and still fails: **41 passed, 9 failed**, log apps/desktop/build/preflight-targeted3.log. Hand-off checked actual final log. Failing list includes four short-shell cases in app_shell_layout_test.dart (1200x360 DPR1; 1200x480 DPR1/1.25/2) plus five others; inspect full log for exact errors. Do not report suite green. Previous logs build/preflight-suite.log and preflight-targeted*.log may help. No Flutter command should still be active from this run (verify if necessary).

Production UI is OdessyApp/main.dart. Normal disconnected entry vs production --demo fixture vs standalone tastecode_demo must be distinguished. Old debug executable proves nothing. Preserve original docs/view images; new captures need new subdirectory and manifest. 03 screenshot manifest was corrected against 29 PNGs and seven source hashes. Human visual acceptance remains pending; preflight does not establish beta readiness.

## Cached tools / commands (no downloads)

Python default is broken Cygwin; use cached Windows interpreter:
C:/Users/13647/AppData/Roaming/uv/python/cpython-3.13.15-windows-x86_64-none/python.exe

From integration root, PowerShell:

    $env:PYTHONPATH = (Join-Path (Get-Location) 'src')
    & 'C:/Users/13647/AppData/Roaming/uv/python/cpython-3.13.15-windows-x86_64-none/python.exe' -c "import site; site.addsitedir(r'C:/Users/13647/OneDrive/Desktop/MiMundo/GitHub/odessy/odessy/.ci-venv313/Lib/site-packages'); import pytest; raise SystemExit(pytest.main(['tests/test_agent_basics_integration.py','-q']))"

Use site.addsitedir, not only PYTHONPATH: pywin32 .pth required. Cached interpreter invocation required tool sandbox escalation earlier. SQLite FTS5 available. No package installation needed.

Flutter .bat fails Git PATH; invoke directly from integration/apps/desktop:

    $env:PATH = 'C:/Users/13647/develope/flutter/bin/cache/dart-sdk/bin;' + $env:PATH
    & 'C:/Users/13647/develope/flutter/bin/cache/dart-sdk/bin/dart.exe' 'C:/Users/13647/develope/flutter/bin/cache/flutter_tools.snapshot' test --no-pub --reporter expanded

SDK lock access required escalation earlier. Serialize Flutter tool use. PATH is necessary for native instance-guard subprocess tests. package_config rebased already using tool/prepare_agent_basics_cache.ps1 -CacheCheckout ../../odessy. Cached original package_graph.json copied into ignored .dart_tool. Do not download dependencies. Direct dart analyze can use the same dart executable. Native build plugin metadata may need existing cached .flutter-plugins-dependencies copy; avoid unnecessary builds for preflight.

## Finish sequence

1. Verify status/workers and read contracts/ownership. Inspect diffs and failing logs. Preserve unrelated work.
2. Fix concrete backend lifecycle/workspace/mode/persistence/context blockers with focused regression tests. Recheck scope of each capability flag.
3. Fix actual remaining shell regressions or identify exact blocking failures. Run appropriate focused checks, then combined backend and agreed Flutter gate; do not repeatedly rerun already green checks without reason.
4. Obtain fresh read-only review if useful. No sibling reference source may become undeclared dependency.
5. Update contracts and ownership with exact trusted resolver/lifecycle seams and 04/05 integration instructions. Write doc/reports/06.md with exact gate time, source revision, checks, unresolved requirement IDs, and source ownership release. If blocked, mark NOT READY; do not start 04/05.
6. Commit exact scoped changes locally, diff --check and verify clean state. If READY, create isolated feat/agent-basics-04/-05/-06 branches/worktrees from the verified integration revision (check existence first), give start instructions. Do not implement 04/05 or the remaining full 06 client UX yourself in this preflight handoff.

Wave 2 later includes B02–B09 complete durable history/client mode UX and B10–B14 context/memory/RAG/terminal/checkpoint UX against frozen contracts; 07 owns final backend wiring and continuation matrix enforcement. Preflight READY is not whole-feature completion or release approval.
