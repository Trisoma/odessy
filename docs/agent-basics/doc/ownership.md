# Exclusive ownership and isolated execution — contract v1

Status: FROZEN; exact Wave1 base/worktree receipts appear in reports/00.md. Parent workspace W=C:/Users/13647/OneDrive/Desktop/MiMundo/GitHub/odessy. Main UI checkout W/odessy stays untouched. Integration checkout W/.worktrees/agent-basics-integration on feat/agent-basics-integration. All source paths below relative to EACH worker checkout, never the original UI checkout.

## Wave1 exact write sets

| Owner | Branch / checkout | Exclusive source/test paths |
| --- | --- | --- |
| 01 | feat/agent-basics-01 / W/.worktrees/agent-basics-01 | src/odessy/application/conversations/**; tests/test_conversation_*.py; tests/conversations/**; docs/ui/conversation-history.md |
| 02 | feat/agent-basics-02 / W/.worktrees/agent-basics-02 | src/odessy/application/access_modes.py; src/odessy/application/access_mode_routes.py; tests/test_access_mode*.py; docs/ui/access-modes.md |
| 03 | feat/agent-basics-03 / W/.worktrees/agent-basics-03 | apps/desktop/lib/app/app.dart; apps/desktop/lib/app/theme.dart; apps/desktop/lib/app/preferences/**; apps/desktop/lib/features/shell/**; apps/desktop/lib/features/conversation/presentation/**; apps/desktop/lib/features/workspaces/presentation/workspace_inspector.dart; apps/desktop/lib/widgets/**; apps/desktop/test/layout_*.dart; apps/desktop/test/agent_basics_layout_test.dart; apps/desktop/test/tastecode_wired_test.dart; apps/desktop/test/design_glass_test.dart; docs/view/agent-basics/wave1/** |

Existing tests outside write set are read/run only; a necessary correction is a handoff recipe to integrator. 03 may create presentation-only helper widgets under owned paths, not new runtime controllers. Preferences must preserve existing strict schema via backward-compatible migration or separate revisioned layout file. Grant/credentials not preferences. No worker writes manifests/public exports/foundation types, session/api/bootstrap/agent_loop or shared client/session/project controllers. 03 app.dart ownership explicitly overrides earlier generic composition reservation, but must consume existing public interfaces and provide integration recipe for new services.

00 owns foundation only: application/agent_basics.py, create_app optional mount in api.py, client/src/agent_basics.dart and public export, foundation Python/Dart tests. 06 may extend these after Wave1 stops; during Wave1 they are frozen. New DTO changes are coordinated report requests, never competing versions. Workers implement shared routes as standalone register(app,deps,service=...) modules and test via AgentBasics registrar closures; no api.py edit needed.

## Wave2 ownership (06 freezes exact base after preflight)

04: src/odessy/application/context/**, memory/**, retrieval/**; tests/test_context_*.py,test_memory_*.py,test_retrieval_*.py; docs/ui/context-memory-retrieval.md. All extension tables/migrations there, no core store schema edits.

05: src/odessy/application/terminals/**, checkpoints/**; tools/src/agent_basics_terminal/**; tools/tests/agent_basics_terminal*.test.ts; tests/test_terminal_*.py,test_checkpoint_*.py; docs/ui/terminal-checkpoints.md. Dedicated helper only, no npm manifests/locks/main server edits; supply07 precise dependency/resource recipes. PTY missing dependency is an explicit gate.

06: shared application/api.py,session.py,bootstrap.py,serve.py,agent_basics.py; runtime/agent_loop.py, authority/action_approval.py only necessary integration; packages/odessy_client/lib/** and test/**; apps/desktop/lib/app/**; features/workspaces/controllers/project_catalog_controller.dart; new features/history/**,context/**,memory/**,retrieval/**,terminals/**,checkpoints/**,access_modes/**; existing shell/conversation/inspector presentation after03 stops; tests/agent-basics-integration/**; tests/test_agent_basics_integration.py; associated desktop integration tests. Avoid editing04/05 backend modules while they write. Existing01/02 modules frozen unless worker release/integrator sequencing permits needed correction.

07 owns all integration corrections only AFTER all Wave2 writers stop, manifests/lockfiles/setup inventory/packaging, final tests and docs/view/agent-basics/final/**. It reviews/cherry-picks worker commits sequentially, no automatic develop merge/push. Keep user UI merge separate. No rebase of running workers. If UI branch squashed/deleted, use recorded SHA/content and reconcile develop later.

## Reports and commits

Pack absolute location W/restructure/agent-basics is OUTSIDE app/worktrees. Each agent writes ONLY its doc/reports/NN.md;00 owns contracts/ownership/capability-map.06 owns Wave2 base update in ownership after01–03 stop;07 final status. No shared progress file. Stage explicit own paths, inspect staged diff and commit locally; record base and commit SHAs. Never commit another agent's work or generated cache/profile. Include integration recipe and unresolved tests. No cleanup/deletion of branches/worktrees.

## Cache/tool/test queue

Python3.13.15 exists at C:/Users/13647/AppData/Roaming/uv/python/cpython-3.13.15-windows-x86_64-none/python.exe. Default python points to broken Cygwin; .ci-venv313 launcher references missing interpreter. Cached dependencies W/odessy/.ci-venv313/Lib/site-packages work when activated using site.addsitedir (not only PYTHONPATH: pywin32 .pth required). Sandbox may require approval to execute cached interpreter; successful invocation recorded by00. Do not install/download alternatives silently. SQLite3.53.1 FTS5 verified.

Python tests in separate worktrees/profiles/ports may run concurrently. Set PYTHONPATH to YOUR src; use site.addsitedir(cached_site) then pytest.main(...). Test temp files inside worker build/agent-basics/NN or OS temp; never production profiles.

Flutter/Dart cached at C:/Users/13647/develope/flutter/bin; Node C:/Program Files/nodejs/node.exe. Workers have no generated caches initially. 00 provides script tool/prepare_agent_basics_cache.ps1: rebases existing Dart package_config to local worktree and cached absolute dependencies, no network.03 owns exclusive Flutter test/build slot during Wave1;01/02 do not run Dart/Flutter.06 owns Dart/Flutter slot Wave2;04/05 Python/Node only.07 runs complete checks sequentially. Do not run pub get without --offline and explicit need; ordinary Dart test may try writing global cache, use direct cached test runner as recorded. Flutter engine native files/build outputs may need regeneration; never copy stale executable as evidence.

Windows SDK/cache contention: no concurrent native/package builds.05 Node tests may overlap06 Dart tests only with distinct ports/profiles; avoid changing shared node_modules.07 owns installer outputs and fresh isolated profile. Never interrupt unrelated processes.

Frozen Wave1 base: 61438b14064513f5a6c0055bc370c5ba376a67d7. All three worker branches/worktrees above were created and verified from it by00. Integration and original UI checkout remain clean. See reports/00.md for commands and receipts. No worker feature execution has begun.

## Live 06 preflight status — November 14

The preceding clean/no-worker statements are historical00 receipts.01–03 are stopped and their listed commits were integrated through22c8a49 in the integration checkout.06 owns the shared integration changes and legacy-test reconciliation, including the small stable scroll keys in settings/devices views and recorder lifecycle correction. The original UI checkout is not an integration target and was observed independently on develop at841a2c8;06 did not switch or modify it.

The first-pass NOT READY gate is superseded by **WAVE2 READY at defd6e48bad835b40fe3f40ea3e54016e875951d**, recorded in reports/06.md on November14 at16:05.04/05/06 are released only in their own isolated worktrees from that exact base. Existing exclusive write sets above remain in force. Read the final frozen context/adoption/attachment/authorization seams in contracts.md; do not code a competing owner/resolver or edit integration files concurrently.

Frozen Wave2 branches/checkouts (all base `defd6e48bad835b40fe3f40ea3e54016e875951d`):

| Worker | Branch | Checkout |
| --- | --- | --- |
| 04 | feat/agent-basics-04 | W/.worktrees/agent-basics-04 |
| 05 | feat/agent-basics-05 | W/.worktrees/agent-basics-05 |
| 06 client | feat/agent-basics-06 | W/.worktrees/agent-basics-06 |

Preflight creates and verifies these empty worker checkouts; it does not execute their prompts.06 owns the Flutter slot;04/05 do not run Flutter.07 remains the later integration/registration owner after writers stop. Integrator checkout stays on feat/agent-basics-integration. No checkout/branch cleanup or push is authorized by this receipt.

## Checkout provisioning correction after interruption

Final read-only verification: integration and agent-basics-04 are clean at defd6e48bad835b40fe3f40ea3e54016e875951d. The Git provisioning command was interrupted during agent-basics-05; its worktree remains locked as initializing, with an incomplete index (1344 status entries). Do not treat these as user deletions or start05 there. No agent-basics-06 branch/worktree was created. No Git process remained in the final process check. The source preflight READY gate and passing tests remain valid, but worker checkout provisioning is PARTIAL.04 can start;05/06 require finishing their isolated checkout setup first. Preserve the incomplete checkout; do not reset/clean user files. The checkout delay exceeded the additional30-minute budget; no further implementation was performed after that discovery. No push or develop merge occurred.

## Worker checkout setup completed — November 14, 2026

This supersedes the interrupted-provisioning correction above. agent-basics-05 and agent-basics-06 are now complete, unlocked and clean on feat/agent-basics-05 and feat/agent-basics-06 respectively, both at defd6e48bad835b40fe3f40ea3e54016e875951d. The05 files were already complete: the abandoned zero-byte index.lock was preserved as index.lock.interrupted-20261114 after checking no Git process was active; read-tree HEAD repaired the unfinished index without changing source files.06 was created in the agreed sibling directory and populated from a local archive of that exact commit. No existing files were overwritten, no resets/cleanups/pushes/merges or04–06 feature work occurred.

06 cached Dart package configurations were rebased with the existing tool/prepare_agent_basics_cache.ps1 and cached package graphs reused; no downloads or dependency installations.05 shared baseline: tests/test_agent_basics_preflight.py passed2 tests in4.79s.06 focused Dart analysis of session_controller.dart/conversation.dart was invoked from its own checkout. The existing verified full-suite receipts remain in06.md and were not needlessly repeated. All workers can now start in their exclusive checkouts, following frozen contracts/ownership; only06 owns Flutter checks.05's actual interactive PTY dependency gate remains explicit and separate from checkout readiness.

## 04–06 source ownership released after closure audit

Audited local tips: 04 feat/agent-basics-04 = 817c18480ba0b3c83d0eed142c3e079baa02941a; 05 feat/agent-basics-05 = a806f6146b3d2faf2cbe38922850e5439c4fd23a; 06 feat/agent-basics-06 = ed1ff3836a5ee4071a2ab941d349d328d74f8775. All retain the frozen defd6e4 base and are ready for 07's sequential review/cherry-picks, including original and audit commits. This audit did not merge them. Their exclusive source ownership is released to 07 after this receipt; no active writer should continue without coordinating new commits. See final reports for exact evidence and the PTY packaging/capability gate.

Final 06 tip after the handoff-only documentation correction is 171a43c7286dd6a8a20c9a539657683b723e315c; it contains ed1ff38 and 13c5d32. The 04/05 tips above are unchanged.
