# Prompt 05 — production integration

2026-11-14, Asia/Shanghai. **PARTIAL against the entire Prompt 05 acceptance scope.** Integrated source is reviewable and regression-tested; the specific remaining evidence/feature limitations below prevent a blanket COMPLETE claim.

## Branch audit and integration

Audited actual commits and clean worktrees before integration: 01 `feat/tastecode-ui-demo` 253ab3e, 02 `feat/ui-settings-wiring` 8b72ecc, 03 `feat/ui-workspace-wiring` eb67d78, 04 `feat/ui-devices-voice-wiring` bb59652. They contain the assigned foundation/settings/workspace/device-voice modules and tests. Their reports explicitly reserve production composition for 05; they were not standalone complete product integrations.

Created `feat/ui-wiring-integration` in sibling `odessy-wt-integration` and merged all four branches without conflicts. Existing branch worktrees/user changes/backups/stashes preserved. `develop` remains db2ae5fd1c11fb0f63cf7ba2083ba557b403313b locally, untouched. Remote develop was observed at a different commit; it was not updated. No force push.

02 audit found corrupt-load/default and save/reload/discard races; repaired on 02 as 45355ed. A further unrelated-section loading guard repair is cd11cb5; both merged sequentially into 05. No changes were needed on 01/03/04 before merge. Current user instruction explicitly authorizes commits, pushes and feature integration, overriding the pack's older prohibition.

## Production changes and real boundaries

- Local preference controller now owns app theme, OS-composed text scaling and opaque glass; Save/Cancel/Reload and conflicts remain honest. Explicit DemoApi/capture composition never persists preferences to the user store. Legacy visual test now injects its own store.
- Project/session catalog is reachable in the sidebar and Workspaces. Configured-root editor remains separately reachable. Session selection snapshots the requested live idle session, replaces its event subscription without replay/create/delete, and preserves per-session drafts. Grouped New retains live sessions. Shutdown/transport replacement clean only sessions this desktop created, not borrowed selections. Runtime sessions remain process-scoped.
- LocalApi exports/implements WorkspaceInspectorApi through its existing authenticated bounded no-redirect transport. Production routes register in create_app, resolve only explicit configured session workspace owners, check live resources/local target and rebuild policy on use. No caller-supplied root or monitoring-target fallback. Unknown/unavailable/closed/changed roots fail closed. Read policies, hooks, sensitive paths, symlink/junction escapes and size bounds remain server-owned.
- Custom approval plans gain the worker's content-bound review before broker prompting; the existing plan.matches/_BoundArguments dispatch guard rejects changed file content or authority. UI rechecks exact pending sid/request/path/content hashes and uses the existing approval endpoint. No new write/permission service. Unsupported/new-file proposals retain established approvals without fabricated diffs. Harness proposed-change capability remains false.
- Inspector follows current snapshots, invalidates on disconnect/session change, projects actual settled outputs without command input/PTY, and supplies explicitly reviewed bounded text-file attachments to the existing once-only text send. Failed/stale context retains draft and attachments. No remote workspace inference.
- Device hub uses the existing inventory owner instead of duplicate polling; voice release occurs on navigation/session/connection transitions. Rich voice options, pairing, execution-host controls and all eight settings groups stay reachable.
- Handoffs now presents two responsive step cards and review area; existing start/run/artifact/continue/cancel/uncertainty controls and controller authorization are retained.

## Verification

No dependency installations/downloads, models, providers, microphone, remote hosts or installers. Offline pub resolution reused caches. Cached Python 3.13.15 plus existing site-packages was used because the old venv launcher points to a missing interpreter. site.addsitedir initializes existing pywin32 .pth files.

| Check | Result |
| --- | --- |
| Worker focused Flutter modules | 47 passed |
| Initial session-directory/inspector Python | 11 passed |
| Python inspector/approval/both-runtime suites | 90 passed |
| Authority/Harness-stream/stop + composition audit | 84 passed (before final stricter owner predicate) |
| Final composition/inspector/approval | 26 passed |
| Application API/bootstrap/voice | 26 passed, one 3-second question timeout under concurrent load; isolated bootstrap rerun 3 passed |
| Full desktop final run | **567 passed, 12 skipped** |
| Client full suite | **50 passed, 2 skipped** |
| Render/capture production composition | passed; 22 actual PNGs, desktop/narrow |
| Runtime settings inventory | 107 validated |
| Existing installer source snapshot (no build) | 225 files; inspector included; fixtures/reference/sibling trees excluded |
| git diff --check | clean |

Commands: from apps/desktop `flutter test --no-pub --reporter expanded`, `dart analyze lib test`, `dart format --output=none --set-exit-if-changed lib test`; from packages/odessy_client `dart test`, `dart analyze lib test`, same format check; from root `python tool/check_desktop_contracts.py`, pytest on test_session_directory, test_workspace_inspector, test_workspace_composition, test_action_approval, test_authority_boundary, test_runtime_backends, test_harness_runtime, test_harness_stream, test_harness_stop, test_application_bootstrap, test_application_api, test_desktop_voice_api. Windows test PATH includes the cached dart-sdk/bin for the existing subprocess instance-guard check.

Initial full runs exposed preference loading/section navigation and fixture storage issues; repaired and rerun. Device assertions now inspect actual selected state rather than counting duplicate labels/expecting placeholder text. No tests disabled. Desktop/client analysis: no issues. Formatting applied. Final affected tests: 16 passed. Latest capture passed. Logs remain in the Codex task outputs directory.

## Preservation matrix

| KEEP | Before → after reachable path | Evidence |
| --- | --- | --- |
| 01 | Activity & usage → primary sidebar/drawer | desktop_application, wired capture |
| 02 | Usage page → same rich history dashboard | usage_dashboard; capture06 |
| 03 | Assistant transcript/details/copy/latest/refresh → same page | conversation_presentation + session tests |
| 04 | Tools → Jobs → same controller/page | jobs_page; jobs screenshot |
| 05 | Tools → Handoffs → two cards + retained run details | handoff_page; handoffs screenshots |
| 06 | Header/config banners/new session → same + live grouping | local_desktop_application/navigation/session tests |
| 07 | Header/General → durable appearance + OS scale | preferences tests; settings01 |
| 08 | Settings → Models → all existing components | settings/model regressions |
| 09 | Settings eight groups → same richer fields | full desktop; 107 inventory |
| 10 | Save/review/dirty/exit/restart → same guarded owners | settings_shell_guard/source/installed tests |
| 11 | Workspaces → Workspace configuration | project_catalog + workspace regressions |
| 12 | Devices → overview/performance/detail + Connect device | device/monitoring/execution tests; captures08–10 |
| 13 | Tools → Local pairing tools | pairing/maintenance regressions |
| 14 | Assistant → Voice options + cancel banner | voice lifecycle/controller/presentation tests |
| 15 | Settings Installation + Tools → install/manage/recovery | installed_manage/setup regressions |
| 16 | main.dart/owned startup/restart/exit unchanged entry | installed_startup/shell/source/session regressions |

## Exact files

- `apps/desktop/lib/app/app.dart`
- `apps/desktop/lib/app/session_controller.dart`
- `apps/desktop/lib/features/conversation/presentation/conversation.dart`
- `apps/desktop/lib/features/handoff/presentation/handoff_page.dart`
- `apps/desktop/lib/features/shell/presentation/device_sidebar.dart`
- `apps/desktop/lib/features/workspaces/controllers/workspace_inspector_controller.dart`
- `apps/desktop/test/app_shell_layout_test.dart`
- `apps/desktop/test/session_controller_test.dart`
- `apps/desktop/test/settings_shell_guard_test.dart`
- `apps/desktop/test/visual_preview_test.dart`
- `apps/desktop/test/workspace_inspector_test.dart`
- `packages/odessy_client/lib/odessy_client.dart`
- `src/odessy/application/api.py`
- `src/odessy/application/bootstrap.py`
- `src/odessy/runtime/agent_loop.py`
- `apps/desktop/test/tastecode_wired_test.dart`
- `packages/odessy_client/test/workspace_transport_test.dart`
- `tests/test_workspace_composition.py`
- `doc/ui/tastecode-wired/README.md`
- `doc/ui/tastecode-wired/*.png`

Pack control-inventory updated with row-level integration status; this report copied into repository doc/ui/tastecode-wired for committed review. Screenshots and reproducible capture command: [README](../../../../odessy-wt-integration/doc/ui/tastecode-wired/README.md).

## Remaining gates / narrow recommendation

Source is suitable for review on the integration branch; do not call this the complete beta or installed qualification. No native desktop launch/live Python→Dart→controller→view chain was demonstrated in one test: backend ASGI boundary, client HTTP and production-view fixtures were verified separately. Direct activity-event links into the inspector remain unimplemented (the conversation inspector button is functional). Dedicated denied-device production-shell screenshot remains absent (denial/revocation behavior has service/controller/widget tests). Full native keyboard/IME/accessibility matrix and every preference scale/platform combination were not exercised. Harness exposes no invented terminal stdout/proposed diffs; SSH source is absent on this base and remains unsupported. No interactive terminal, durable conversation replay or cross-engine migration was added. Installed/hardware/signing/release and user acceptance are separate, unrun gates.

No execution/provider/permission grants were added by opening UI. No runtime dependency closure or security rules changed. Fixtures remain test-only. Hosted CI results after push remain unobserved.

Final commit dc423c9 was pushed and remote hash verified on feat/ui-wiring-integration. All four worker branches were also pushed. develop was not updated. UI visual adaptation remains incomplete: the integration retains much of the old production page styling rather than the approved demo layout/typography; the user explicitly flagged this discrepancy. Do not treat passing functional tests as visual parity.
