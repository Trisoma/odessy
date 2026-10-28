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
The Phase 2 follow-up now implements startup/saved state, reviewed restart,
field validation/merge presentation and source crash recovery; see
[its current contract and qualification limits](running-configuration.md).
Native/package acceptance remains open. Do not mark the full UX overhaul complete
from these milestones.

## October 28 parallel implementation contract

Codex owns Python/Rust, `packages/odessy_client`, packaging and shared docs.
DeepSeek owns `apps/desktop` during parallel work. No existing field is removed.

- Local helper operation `component-probe` accepts the displayed `selection` and
  parameters `section` (llm/stt/tts/voice/host), `run` (default false), `consent`
  (must be exactly true for inference/audio), and optional `audio` (STT WAV path).
  Omitting STT audio records up to five seconds. No coordinator is required.
- Observational results contain `status`, `message`, `section`, `fingerprint`,
  `inference_performed:false`; voice inventory also returns `devices` with
  `id/name/input/output`. Inventory does not open a microphone. Native device IDs
  bind the host API and name; duplicate or disappeared selections fail explicitly.
- Explicit successful probes add `tested_at` (Unix seconds), `elapsed_ms`, optional
  bounded/redacted `text`, and for LLM `provider/model/usage/accounting`. This is
  evidence for displayed values, not proof that the coordinator applies them.
  Each helper is a separate diagnostic run using existing API accounting/egress;
  it does not share/reset the coordinator's process budget. Editing invalidates
  displayed evidence. No automatic retry, repair, provider substitution or tools.
- Additive errors retain `reason` and can include `code` (validation, credentials,
  unavailable, cancelled, unsupported). Clients must tolerate unknown codes.
- `selection.voice` additionally persists `audio_backend` (auto/native/command),
  `audio_input_device`, `audio_output_device`, `wake_model`. Defaults preserve the
  existing Linux command path; auto uses native audio on Windows/macOS.
- Session voice actions additionally accept `enable_wake` and `disable_wake`.
  Snapshot `mode` is push_to_talk or wake; `state` and `recorder_host` identify the
  actual microphone owner. Wake is opt-in, uses the existing broker and session,
  and never starts automatically. Stop playback preserves wake listening.
- Pair operation `grant` optionally accepts `capability` (mcp by default, or
  telemetry.read). Only mcp needs an exact existing local workspace. Native peer
  records add boolean `revoked`; absence means unknown, not paired. Discovery
  advertisements never establish identity, permissions or connection health.

Mutation review tokens, configuration revisions, capture IDs, trusted approval
channels, origin-bound authentication and no-replay behavior remain unchanged.

## October 28 integration corrections (no new wire fields)

- Monitoring cards consume `observation.schema.json` v1: device lists are named
  `devices`; network rates are `rx_bytes_per_second`/`tx_bytes_per_second`;
  disk free space is `available_bytes`. Missing values are never zero-filled.
  A failed refresh marks retained measurements historical rather than current.
- Credential observation uses the runtime's effective environment-key precedence
  and rejects whitespace-only keys. This is presence evidence, never authentication.
- Session snapshots redact credential-named values, including numeric/container
  credentials; established numeric token-accounting fields remain visible.
- Existing `restart_required` on helper load is a legacy default, not active-state
  evidence. Compare saved and service-owned section fingerprints; unavailable
  service evidence remains unknown. Save never changes the startup descriptor.
- Credentials remain inherited provider environment references and memory-only
  connection tokens. No durable OS vault or GUI key-entry support is added.
- An explicitly selected `credential_env` is authoritative: a missing referenced
  key cannot silently fall back to another provider/global key. The value is
  resolved only inside the diagnostic/runtime process, never in saved settings.

## October 28: selectable LAN discovery

`pair:discover` now returns additive `devices` entries with `name`, `address`,
`advertised_fingerprint`, `usable` and `trusted: false`. It runs the matching
odessy-link `discover --seconds 2 --json` (schema_version 1). Existing discovery
text output remains available without --json; older link binaries need updating.
Names and advertised fingerprints are unverified metadata. Selecting a result
fills the address and invalidates review; it never enrolls or grants access.
First enrollment still requires independently verified identity and exact review.
The headless executor must advertise on the same Wi-Fi/LAN. This is not public
internet discovery; network multicast/firewall restrictions may prevent results.
Wildcard listeners advertise available interface addresses rather than 0.0.0.0.

Verification: Rust 76 passed; Python discovery/native bridge 6 passed; Flutter
nearby selection and desktop acceptance 5 passed. No live two-device discovery,
ARM64 build/deployment or native GUI acceptance was performed in this change.

## Startup recovery (October 28)

A failed session initialization returns HTTP 503 with detail.code
`session_unavailable` and a fixed safe message; raw provider/transport exceptions
are never returned. No replacement model, transport or tool session is created.
Settings, runtime inspection and native pairing operations remain independent of
conversation startup. Existing status-only clients remain compatible; new clients
label 503 as conversation unavailable and offer Settings/Devices navigation.
Revoked peers remain in inventory and must not be connected or silently trusted.

Verified on Jetson (October 28): the actual revoked laptop registry was readable;
no-inference session initialization returned 503 and settings/profiles/capabilities
returned 200. ARM64 build and Python 3.10 compilation passed; source and desktop
were deployed after backups. This is not a successful re-pairing or tool-execution
qualification. Trust and permissions were not changed.

## Chat-only recovery when the configured executor is unavailable

When paired execution cannot initialize, a composed session may start with the
same selected model in chat-only mode. It emits additive event
`execution_unavailable` with a fixed safe `message`. Existing clients ignore
unknown events; new clients display a persistent nonfatal notice. No tools,
workspace hooks, collaboration/delegation jobs or substitute local executor are
attached. Model/provider failure still fails the request. Execution is enabled
only by a fresh explicit reconnect/session after verified host recovery.

Chat-only follow-up verification on Jetson: actual revoked registry now permits
session creation (201), fake-model chat, and settings inspection while rejecting
tool execution and delegated jobs. This supersedes the earlier 503-only recovery
behavior for unavailable paired execution. Healthy sessions retain existing policy
and trust revalidation; unavailable model/provider initialization still fails.
