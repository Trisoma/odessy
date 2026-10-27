# Desktop UX foundation contract — October 27, 2026

Scope: M1 navigation and shared layout on `develop`, starting from `f016a7c`.
This is an implemented presentation contract, not a new HTTP protocol or release
acceptance claim. The parent-folder desktop audit and overhaul plan remain the
broader workstream; their proposed DTOs are not shipped by this milestone.

## Current navigation and action matrix

| Destination | Existing implementation reused | Boundary / remaining work |
|---|---|---|
| Assistant | `ConversationPage`, `DesktopSession`, existing approval/question cards | Internal Conversation route retained. No audio protocol added. |
| Workspaces | Existing `host` component editor and `ComponentSettingsController` | Saved folder/role only; not proof of the running workspace, a registry, or a permission grant. |
| Devices / Overview | `DevicePage` | Live inventory/status reconciliation remains incomplete. Named sample devices appear only in demo mode. |
| Devices / Performance | Existing `MonitorPage` and app-owned `MonitorController` | No new telemetry source; missing metrics remain missing. |
| Devices / Connect a device | Existing native pairing page/controller | Enrollment and grants retain their existing reviews. Selecting a view never retargets execution. |
| Settings | Existing independent model, STT, TTS, voice, host and advanced editors | Existing save/apply behavior retained. Cross-navigation draft retention is a later milestone. |
| Settings / Models / Connect saved configuration | Existing source `SetupController.restore()` or installed application restart callback | Explicit idle-only action. Startup failure remains visible; no demo fallback. Full new-save-to-live-provider acceptance not performed here. |
| Settings / Installation | Existing installation manager, installer engine and runtime-maintenance routes | Native context still determines supported operations; running turns block maintenance entry. |

Primary navigation is **Assistant, Workspaces, Devices**, with **Settings** separate.
Monitoring is a Devices tab; installation and runtime maintenance are Settings
actions. Secondary pages have a Back action. The progressive setup implementation
remains available internally for compatibility, but is not the everyday entry point.

## Shared components and state rules

- `app/navigation.dart` owns route labels and parent relationships; it has no
  execution, network or configuration mutations.
- `DesktopPageScaffold` supplies scrolling, content width and page headings.
- `SettingsScaffold` aligns navigation with the first card, constrains readable
  content width and supports a compact section selector. Cards share width/spacing.
- `ComponentStateBanner` presents observed information/errors, live semantics and
  explicit actions, with bounded adaptive action layout.
- The sidebar becomes a drawer below 900 logical pixels; selecting a destination
  dismisses it. Conversation header content scrolls rather than squeezing the composer.
- Transport labels report connected/disconnected facts only. A connection is not
  installation, model, tool or audio readiness. Demo remains explicitly labelled.
- Navigation does not create a replacement session, select a model, stop a turn,
  answer an interaction or save configuration. Existing pending questions survive.

No Python API, configuration schema, secret storage, policy, approval channel,
execution target, monitoring transport or installation engine changes are included.
The four monitoring brace changes only remove existing Dart analyzer diagnostics.

## Cross-check rulings and dependencies

1. Phase 0 proposes a capabilities endpoint, revisions and desktop audio contracts.
   None is required to regroup existing screens. They remain proposals, and no
   fabricated capability object is introduced to make the new navigation look complete.
2. Independent component editors already exist; another form engine would duplicate
   persistence. M1 adapts those editors. Dirty drafts, app-owned saves, uncertain
   outcomes and saved-versus-active configuration need dedicated controller work next.
3. Removing progressive setup without retaining explicit activation would strand
   source-preview users on restart-only settings. Review caught this dependency:
   Connect saved configuration now reuses the existing lifecycle, under idle guards.
4. Workspaces is currently saved host configuration, not a live workspace manager.
   Devices is a grouping of existing overview, pairing and metrics, not a newly
   unified authoritative inventory. Screens disclose these boundaries.
5. Existing installer-first release text and the owner's portable-preview direction
   need a separate release-scope reconciliation. M1 does not change release gates.
6. The source audit is a snapshot, not a current acceptance certificate. Shared
   controllers/native interfaces require review before subsequent screen work.

## Verified evidence

- Flutter full suite: **127 passed, 5 skipped**; analysis: **no issues**.
- Focused startup/navigation/layout suite: **22 passed**. New regressions cover
  pending interaction/session preservation, independent configuration entry,
  explicit activation failure, maintenance reachability, drawer/keyboard behavior,
  long paths, loading/error states and 100/150/200% text scaling.
- Architecture/config inventory validator: **104 settings validated**; Python
  settings-inventory regression: **2 passed**. No backend test suite claim is made.
- Dart client suite: **11 passed, 2 skipped**. Windows release build: **passed**
  (67.7 s), `apps/desktop/build/windows/x64/runner/Release/odessy_desktop.exe`.
- Widget-rendered screenshots with bundled fonts: `apps/desktop/build/preview/ux-foundation/`.
  Light/dark Settings, Assistant, workspace and Devices/Performance are captured at
  840, 1280 and/or 1440 logical widths. These are test artifacts, not native screenshots.
- Independent review identified the activation dependency above and accepted the
  explicit lifecycle adapter correction. That review is not provider/hardware testing.

Computer Use native inspection was unavailable because its native pipe could not
connect (OS error 2). Native window interaction, OS scaling, packaged install,
real providers, microphone and Linux/macOS/ARM64 acceptance remain unverified.

## Next milestone

M2 component draft ownership and save/recovery is now implemented as described in
[the lifecycle contract](settings-draft-lifecycle.md). App-owned component drafts,
per-service collaboration forms, leave prompts and read-only unknown-outcome
inspection reuse existing backends. Source/installed real-helper checks passed.
The broader Phase 2 remains incomplete: authoritative saved/active readiness,
hard-kill recovery and the remaining validation/merge acceptance are still open.
Do not mark the full UX overhaul complete from these milestones.
