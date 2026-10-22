# Desktop preview · v0.5.0

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

Click the cable icon, enter the loopback address and printed bearer token. The
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

## Ownership and extension guide

Protected foundation: `packages/odessy_client/**`, `lib/app/session_controller.dart`
and connection composition in `lib/app/app.dart`. Chenyang owns feature presentation
and theme refinement through reviewed changes. Presentation imports typed models
and controllers; it must not create network clients, processes, provider SDKs or
native file access. `test/architecture_test.dart` enforces that boundary.

To add a feature: define its DTO/repository contract in the client package, add a
demo fixture and contract tests, expose a controller command, then create a page
under `lib/features/<name>/presentation/`. Cover loading, empty, offline, failure,
busy and success. A missing backend operation stays visibly unavailable; never
replace it with a successful-looking mock in live mode. Do not import a Dart agent
runtime or connect providers directly from widgets.

The local `cc-switch-main` and `agentic_flutter-main` references informed layout
and deliberate approval patterns. No agentic runtime dependency or source code
was copied; these widgets are original and use Odessy's API boundary.

## Checks

```powershell
cd packages/odessy_client
dart pub get
dart analyze
dart test
cd ../../apps/desktop
flutter pub get
flutter analyze
flutter test
flutter build windows --debug
```

`test/visual_preview_test.dart` writes screenshots under `build/preview/`.
For opt-in local service integration, set `ODESSY_TEST_ENDPOINT` and
`ODESSY_TEST_TOKEN`, then run `dart test test/live_api_test.dart` from the client
package. No live provider tests or hardware tests run automatically.

## Verification

See `docs/ui/verification.md` for the commands actually run on this revision.
