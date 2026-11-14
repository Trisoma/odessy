# Prompt 05 and visual integration — final source closure

2026-11-14, Asia/Shanghai. **COMPLETE for production source integration and automated offline verification.** Visual integration is ready for the owner's review; human design acceptance, installed/hardware/signing/release qualification remain separate. No complete-beta claim is made.

## Live branch and preservation

Work remained in the original checkout on `feat/ui-wiring-integration`, base HEAD `511aa0a`. The four worker/demo tips were already consolidated before this task; no merges or branch switching were needed. `develop` was untouched. No commits, pushes or publication in this task. The 24 pre-existing deletions under `doc/ui/tastecode-wired/` were preserved, and the user-selected final evidence destination is `odessy/docs/view`. Original sibling reference screenshots were not overwritten.

## Closed integration gaps

The integrated production shell, not a fixture-only alternate entry point, now carries the approved navy/mint styling, bundled type, 264 px sidebar, project/session search and grouping, visible Activity & usage/Jobs/Handoffs, compact context composer and 390 px adjacent inspector on wide windows. Narrow layouts retain the drawer and a full-width inspector while preserving the shared draft. The boat remains in the shell and assistant messages. Connection status, active-model versus next-turn selection, refresh/session details/copy/latest and voice controls remain reachable.

Pending approval cards keep action, target, permission scope, risk and exact arguments visible. Only extra raw details are collapsed. Responses still use exact existing session/request broker ownership. Inspect diff opens the actual bound proposal panel; activity output links open the existing recorded-output panel, with no execution effect. Both rendering themes retain contrast and screen-reader headings. Handoff's retained real two-step form/review uses the existing bundled monospace font; unsupported arrow glyph labels now use plain words.

All eight settings groups preserve their original rich fields/controllers, exact save/reload/revision/dirty-navigation semantics and separate durable local preferences. No backend production service, route, execution policy, runtime dependency closure or transport behavior changed in this final visual pass. Earlier 01–04 and integration commits already contain the typed client, persistence, inspector authorization and lifecycle foundations; this task uses those owners.

## End-to-end evidence and honest fixture scope

New test-only `apps/desktop/test/support/ui_fixture_backend.py` starts the real production Python API/session/policy/approval broker on authenticated loopback with a deterministic agent provider and one temporary workspace. `production_service_integration_test.dart` crosses Python HTTP → Dart LocalApi → production controller → production widget for session create/list, turn response, file read, sensitive `.env` refusal, command-output projection, exact diff approval with file-byte verification, Stop, workspace revocation and unavailable voice. Provider output is synthetic and no terminal command runs. The temporary-file write is performed by the deterministic fixture after real approval-plan/broker checks; it is not evidence of the Node tool executor. Existing backend tests separately cover actual dispatch/authority and both runtime selections. Voice/monitor lifecycle, persistence and settings are covered by their existing service/store/controller suites; a real microphone/device is not part of this chain.

The real monitoring Dart HTTP regression now exercises 403 after successful metrics, asserting stale metrics/host rows clear and no DemoApi fallback occurs, and captures the denied production page. Screenshot fixtures are explicitly isolated and labeled. All final PNGs use actual production Flutter widgets. Final capture count: **29**; desktop 1600×1050 and narrow 720×900. The README contains the full index, commands, launch prerequisites and fixture limitations. No image generation was used.

## Validation

| Check | Result |
| --- | --- |
| Full desktop suite, real loopback Python selected | **569 passed, 12 skipped** |
| Final capture/service fixture refinements | **2 passed** |
| Dart client full suite | **50 passed, 2 skipped** |
| Backend composition/inspector/approval/runtime/Harness stream/stop/voice/settings suites | **148 passed** in 162.49 s |
| Desktop and client analysis | No issues |
| Desktop/client formatting | Clean |
| Runtime settings contract validator | **107** validated |
| Existing installer source inventory, no build | **225** files; new test fixtures/images/reference trees excluded |
| Windows debug build, no pub/install | Success, final build 21.6 s |
| Native launch | Owned newly built `odessy_desktop.exe --demo` process launched; stopped after smoke |
| git diff --check | Clean |

The initial full run caught old presentation assertions and actual missing accessibility labels after layout adaptation. Corrected assertions follow retained behavior (including transcript activity and Changes counts); restored compact visible approval facts/headings rather than weakening authorization or contrast tests. An installation-review test now scrolls to its retry action before tapping. No tests disabled. Final expanded-activity and populated narrow-diff captures passed after the full source suite. No dependency installations/downloads were performed locally. The existing Desktop preview CI reuses existing `[test,ui]` extras and selects Python so the new bounded service-to-view test runs instead of silently skipping.

Commands from `apps/desktop`:

```powershell
dart format --output=none --set-exit-if-changed lib test
dart analyze lib test
flutter test --no-pub --dart-define=CAPTURE_WIRED=true --reporter expanded
flutter test --no-pub --dart-define=CAPTURE_WIRED=true test/tastecode_wired_test.dart test/production_service_integration_test.dart --reporter expanded
flutter build windows --debug --no-pub
```

Full suite used existing Dart SDK on PATH for the subprocess guard and `ODESSY_UI_TEST_PYTHON` pointing at cached CPython 3.13.15. The old venv launcher refers to a missing interpreter, so `ODESSY_UI_TEST_SITE` selected the existing `.ci-venv313/Lib/site-packages`. Client commands: `dart analyze lib test`, `dart test`, format check. Root contract command: `python tool/check_desktop_contracts.py`. Python used pytest on `test_workspace_composition`, `test_workspace_inspector`, `test_action_approval`, `test_runtime_backends`, `test_harness_runtime`, `test_harness_stream`, `test_harness_stop`, `test_desktop_voice_api`, `test_desktop_settings_inventory`. Existing cached packages were reused.

## Preservation matrix

| KEEP | Before → retained reachable path | Automated evidence |
| --- | --- | --- |
| 01 | Activity & usage → primary sidebar/drawer | desktop_application, shell/navigation, capture06 |
| 02 | Usage dashboard → same filters/history/pagination/charts/tables | usage_dashboard, client usage_history_api |
| 03 | Conversation details/copy/latest/refresh/provenance → compact header/transcript | conversation_presentation, accessibility, capture01/01b |
| 04 | Tools/Jobs → primary Jobs destination | jobs_page, capture jobs |
| 05 | Tools/Handoffs → primary two-step Handoffs + review | handoff_page, wide/narrow captures |
| 06 | Connection/readiness/new-session recovery → retained shell/session owners | local_desktop_application, readiness, session/draft tests |
| 07 | Theme/glass → durable appearance + light/dark/opaque surfaces | preferences, paint contrast, settings-light |
| 08 | Models/profiles/credentials/discovery → Models and connections | model/settings regressions, settings2 |
| 09 | Rich component/collaboration settings → all eight groups | full suite, 107 contract inventory, settings1–8 |
| 10 | Review/save/reload/discard/dirty/restart → unchanged guarded owners | settings_shell_guard, source/installed configuration tests |
| 11 | Configured root/picker/grants → Workspaces configuration | project_catalog, workspace tests, capture07 |
| 12 | Device connect/pair/performance/detail/history → Devices | monitoring, execution-host regressions, captures08–11 |
| 13 | Pair enrollment/trust/grant/revoke → Tools & installation / Local pairing tools | pairing_dialog and maintenance regressions |
| 14 | Availability/PTT/wake/input/output/cancel/playback → Voice options | voice lifecycle/controller/API, capture02 |
| 15 | Installation/manage/recovery/repair/rollback → Settings Installation + Tools | setup_streamlined, installed_manage/setup tests; no installer executed |
| 16 | Source/installed owned startup/exit/restart/single instance → original main/startup owners | full startup/instance/session regressions; installed evidence separate |

## Changed files in this task

- `.github/workflows/desktop-preview.yml`
- `apps/desktop/lib/app/app.dart`
- `apps/desktop/lib/app/theme.dart`
- `apps/desktop/lib/features/conversation/presentation/conversation.dart`
- `apps/desktop/lib/features/conversation/presentation/interaction_card.dart`
- `apps/desktop/lib/features/conversation/presentation/session_activity.dart`
- `apps/desktop/lib/features/handoff/presentation/handoff_page.dart`
- `apps/desktop/lib/features/settings/presentation/settings.dart`
- `apps/desktop/lib/features/shell/presentation/device_sidebar.dart`
- `apps/desktop/lib/features/workspaces/presentation/workspace_inspector.dart`
- `apps/desktop/lib/widgets/glass_surface.dart`
- `apps/desktop/lib/widgets/settings_scaffold.dart`
- `apps/desktop/test/conversation_presentation_test.dart`
- `apps/desktop/test/desktop_application_test.dart`
- `apps/desktop/test/desktop_visual_test.dart`
- `apps/desktop/test/handoff_page_test.dart`
- `apps/desktop/test/monitoring_app_integration_test.dart`
- `apps/desktop/test/presentation_accessibility_test.dart`
- `apps/desktop/test/setup_streamlined_test.dart`
- `apps/desktop/test/tastecode_wired_test.dart`
- `apps/desktop/test/workspace_inspector_test.dart`
- `apps/desktop/test/production_service_integration_test.dart`
- `apps/desktop/test/support/ui_fixture_backend.py`

New evidence: `docs/view/README.md`, `docs/view/integration-report.md`, `docs/view/artifact-manifest.json`, and 29 PNGs. Pack updates: `doc/reports/05.md`, `doc/control-inventory.md`, README status. Pre-existing screenshot deletions are not newly requested cleanup.

## Cleanup and remaining gates

Final fixture shutdown is authenticated and graceful; fallback teardown kills only its owned process on setup/assertion failure. Seven earlier failed-run `odessy-ui-service-*` roots were individually inspected (only fixture `.env`/`readme.txt`, no links), then removed with resolved-temp containment checks. No owned backend test processes remain. The two owned native demo launches were stopped. No user files, unrelated jobs, credentials, D:/Odessy, installers or remote hosts were touched.

The native computer-use service is unavailable (`Computer Use native pipe is unavailable ... os error 2`). Therefore actual widget PNGs and successful native build/process launch are demonstrated, but native window keyboard/IME/screen-reader/platform qualification is not. Automated widget keyboard/focus/text-scale/light-dark checks passed; the full OS/device matrix was not exercised. Human visual acceptance remains with the owner.

SSH implementation is absent on this base; Jetson demo labels are not hardware/SSH evidence. Monitoring-only sources never gain workspace/execution. No PTY, durable transcript replay, cross-engine migration, fictional Harness diffs or new permission system. Source completion does not mean a fresh installed candidate contains these edits. Installed, hardware, signing, clean-machine and human release gates remain separate. Hosted CI after a push is unobserved. No publication or installer build occurred.

The previous PARTIAL report's four source-evidence gaps (visual composition, service-to-view chain, activity inspector links and denied-device capture) are addressed above. Prompt 05 and visual integration can close at the source/automated-review scope; do not carry the older PARTIAL status forward as though those gaps remain.
