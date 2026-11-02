# Desktop preview and handoff backbone · v0.5.0

October 27 navigation milestone: see [the current UX foundation contract](desktop-ux-contract.md)
for implemented routes, current limitations and verification. Everyday navigation
is Assistant / Workspaces / Devices / Settings; Performance is under Devices and
installation actions are under Settings. The older preview record below describes
its original delivery, not the current complete feature inventory or branch state.

Start with [the teammate handoff](handoff.md) and [architecture](architecture.md).
Protected foundation now includes immutable client contracts, lifecycle cleanup,
an ephemeral credential-store seam, reusable async settings forms, a checked
89-setting inventory, development state gallery and accessible glass primitives.
The final verification record distinguishes tested behavior from native/backend
work still deferred.

Flutter/Dart desktop client for Odessy's existing Python session API. This work is
isolated on `feat/flutter-desktop-preview`; it changes no Python, Rust or Node
runtime behavior. Windows, macOS and Linux runners share the same Dart code.

## Try it

Install Flutter stable (this preview is verified against the version recorded in
the verification section below), then:

```powershell
cd apps/desktop
flutter pub get
flutter run -d windows
```

On Linux use `-d linux`; on macOS use `-d macos`. Start in clearly marked **DEMO**
mode without a Jetson, model, credentials or provider account. Type `/approval`
or `/question` to inspect interaction cards. Demo actions execute nothing.
Select devices, switch theme, try Stop during a response, and save/reload the
collaboration limit. Demo settings live in memory only.

For a real local session, from the Python checkout with `odessy[ui]` installed:

```powershell
python -m odessy serve --fake-model
```

Click the cable icon, enter the loopback address and the token you configured in `ODESSY_API_TOKEN` before starting the backend. The backend does not print credentials. The
token remains in memory. Try `hello` or `what time is it?`. Only requests submitted
after connecting use the real service. Use a fake-model service first; omitting
`--fake-model` invokes the configured real models and their existing budgets.

Connection changes require an idle session. Selecting a device only inspects it;
it never changes a conversation's coordinator or execution target. Stop cancels
the owning turn and outstanding interactions; it cannot undo completed tools.
Model selection applies to the **next** turn.

## Implemented preview

- Desktop shell, responsive sidebar/drawer, dark/light themes, keyboard composer.
- Demo fixtures and authenticated, loopback-only HTTP/SSE client.
- Session snapshots, transcript, activity details, model provenance, next-turn
  picker, foreground admission, Stop, reconnect without replaying submissions.
- Broker-owned approval and clarification cards, exact request details, explicit
  approve/deny, allowed-channel and expiry checks, stale-response errors.
- One real settings edit: collaboration `max_consultations`, validated 0–32,
  preserving all other settings/profiles. Applies to newly created sessions.
- Device overview and honest unavailable states for host-management/desktop audio.

This is a preview, not the complete v0.5.0 UI. Full provider/configuration editing,
secure persistent credential storage, host-management API, desktop voice, job/usage
screens, localization, signing and installers remain later milestones. It does
not expose an unprotected remote service or claim cross-platform qualification.

The current settings endpoint has no revision/CAS support. The editor refreshes
before saving and preserves unrelated fields, but simultaneous external writers
can still race; settings revisions belong to the later backend milestone.
# Odessy desktop

The desktop application is a Flutter client for the authenticated Odessy service.
Its presentation layer uses typed controllers; execution, authorization and device
identity remain in the existing Python, Node and Rust components. User-facing UI is
in English. See [handoff](handoff.md), [architecture](architecture.md),
[design](design.md) and [API contract](api-contract.md) for contribution boundaries.
The shared navigation and component contracts are described in
[desktop UX](desktop-ux-contract.md) and [settings draft lifecycle](settings-draft-lifecycle.md).

## Windows local development

Run `apps/desktop/build/windows/x64/runner/Release/odessy_desktop.exe` with its
adjacent DLLs and `data` directory. The local desktop build owns an authenticated
loopback service and a read-only monitor, and stops them on exit. It is specific
to this checkout and depends on its local Python environment and native binaries;
it is not a standalone installer.

No model is downloaded or selected automatically. In **Settings → Models and
connections**, add a model profile, start a new session, and select the profile in
**Conversation**. Real inference requires an available model service and any
necessary credentials. Device-specific remote management, native microphone
capture and audio device selection are not yet available.

Application configuration and trust are stored under `build/local-desktop/state`;
its workspace is `build/local-desktop/workspace`. These are persistent user data.
After source changes, run `powershell -NoProfile -File tool/build_local_desktop.ps1`
from the repository root. The script requires the project-local Flutter SDK, Python
environment, and monitoring and link binaries.

## Validation

From `apps/desktop`, run `flutter analyze --no-pub` and `flutter test --no-pub`.
From the repository root, run `python tool/check_desktop_contracts.py`. Tests that
use fixtures identify them explicitly; live failures must not fall back to mock data.
Changes to shared foundation and native contracts require project-owner review as
described in [AGENTS.md](../../apps/desktop/AGENTS.md).
