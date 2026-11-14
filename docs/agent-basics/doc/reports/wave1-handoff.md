# Wave1 corrected handoff — start06 preflight

November14,2026.01/02 backend slices and03 UI slice accepted for sequential integration, NOT complete end-to-end features. All worker trees clean; no worker writer remains. Frozen base61438b14064513f5a6c0055bc370c5ba376a67d7. Do not rebase/reset active work or automatically merge develop.

Use integration worktree C:/Users/13647/OneDrive/Desktop/MiMundo/GitHub/odessy/.worktrees/agent-basics-integration on feat/agent-basics-integration. Inspect status before any mutation. If another integrator already started, reconcile its existing work rather than duplicate cherry-picks.

Review then cherry-pick IN THIS ORDER, using every commit (or source-verified equivalent if already applied):

01:051acd15f8247654f0cfdc8821fdf3fd0787fa49 then a402d79f70da8de562ab58c9cb910d5abd083755.
02:b9bffde (resolve full SHA via git rev-parse feat/agent-basics-02).
03:1237f5e5a8f26101cf0ef104a0da40a961ef0a43 then7fece34dd9ba1fcc23a364ea55325a6424fd9357 then a872cf3 then74a962ffa870d3ad4cbd0b5edff404e843afd5f2.

01 latest fix: stale/missing delete causes no derived cleanup;31 tests pass. Callbacks inside write transaction must not start nested same-DB writes.04 should use FK cascades/transaction-aware cleanup rather than new connections in callback.02:40 tests pass; normal service dispatch wiring still06.03:12 owned tests pass;29 PNG hashes and7 source hashes verified. Full desktop suite unresolved527pass/13skip/53fail; not all are proven selector-only. Include Dart SDK bin in PATH for native instance-guard probe. Update selectors without weakening dirty guard/focus/accessibility assertions, rerun and fix actual defects.

06 MUST mount and exercise real persistence/restore/review/validation endpoints through shared lifecycle; missing modules must not advertise capability. Harness history-only/permissions-unavailable remains explicit. Native/installed/visual-user/CI gates separate. Do not declare WAVE2 READY until required preflight integration checks pass. Then freeze new base and create04–06 worker worktrees perCOMMON; only then04/05 start.

No source implementation of06 was performed by closure agent; integration checkout untouched. Read individual01/02/03 closure addenda and execute06-client-integration.md.
