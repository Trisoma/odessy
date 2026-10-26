# Desktop preview and handoff backbone · v0.5.0

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

See [verification](verification.md) for commands actually run on this revision.


## Source-preview setup to a live session (October 26)

The six-step **Set up this computer** flow now ends with **Save and connect**.
Entering the final step runs read-only review/diagnostics automatically. The final
button refreshes the exact review, saves once (or inspects a matching existing
export), verifies the saved bytes, and starts an authenticated loopback coordinator
through the existing service composition. JSON/environment exports both work;
raw response JSON is collapsed under **Advanced details**. Execution-only and
unconfigured selections save without inventing a running model.

Fields remain editable after failure; uncertain saves inspect their original
location before accepting edits. Existing directories or conflicting files are
never overwritten. Connection failures leave a disconnected client, not fake
responses. Retry does not repeat a successful save. Cancel/close stops the owned
helper/coordinator; ordinary authorization and execution-target rules still apply.
The source setup connection uses the existing default workspace (`~/Odessy` on
Linux/macOS, `Documents/Odessy` on Windows), not the installation folder.

Normal launches now start disconnected and restore the last successfully connected
source configuration from `~/.config/odessy/desktop.json`. The pointer contains a
validated selection, export location and checksum, never a bearer token. Modified
exports require fresh review. Existing `~/odessy-setup.json` or the generated
`~/odessy-runtime.env` can bootstrap older source previews. No tool/model request
is replayed on reconnect. Demo data requires the explicit `--demo` launch flag.

Settings uses this same source configuration instead of assuming every source
preview is an unconfigured managed installation. Configured-but-offline and
unconfigured are distinct states. Successful navigation never cancels the owned
coordinator; application cancellation/shutdown still stops it.

Voice setup checks known local model folders, configured paths and the executable
PATH, with a two-second/4,000-entry/depth-three scan bound. A unique matching file
fills its field; ambiguous/missing files remain selectable through Browse. No
model download, inference or microphone access occurs during discovery. Detection
is observational and does not prove engine compatibility or microphone readiness.

Source builds still require Python with Odessy and its UI dependencies. This does
not supply missing packaged runtimes; standalone distribution needs the complete
runtime payload.

Verification: full desktop suite **108 passed / 4 skipped**, changed-file Dart
analysis clean, Windows release build passed. **39 Python tests passed**, including
real saved-configuration coordinator startup, authenticated API access and stdin
shutdown without inference. Jetson/Linux ARM64 visual/audio acceptance was not run.
Both Python source and Flutter executable must be updated for this change; a
Python-only copy cannot update buttons already compiled into the old desktop.

### Cloud configuration corrections (October 26)

Changing a saved source configuration now reviews and saves a new sibling file
when its content differs. The original remains intact; the reviewed revision is
the one launched. Matching content reuses the existing file. Editing fields alone
does not reconfigure a running coordinator: use **Save & connect**.

The next-turn picker displays the configured model/provider beside its profile
alias. `current`, `local`, and `auto` are profile names, not a list of cloud model
IDs. Failed turns display the server-sanitized error instead of only a generic
diagnostics instruction. DeepSeek uses API model ID `deepseek-flash`.

Verification: 174 affected Python tests passed; full Flutter suite 111 passed,
4 skipped; changed-file analysis and Windows release build passed. The HTTP
roundtrip uses a real configured coordinator with a mock cloud endpoint; paid
provider, Jetson and audio acceptance were not performed.

### Model choices and conversation avatar

Source setup and installed first-run cloud setup now offer a model picker.
Provider-default suggestions come from the Python registry without network traffic.
**Fetch available models** explicitly reads the selected provider's metadata using
existing environment credentials. Display names are separate from saved API IDs.
Custom IDs remain supported. Listing does not establish model/tool compatibility;
missing credentials or unsupported endpoints leave an actionable message and the
provider-default suggestion. No inference or model download occurs during listing.

Metadata uses an independent helper so it cannot occupy an installation transaction.
The original pixel boat from `installer/ui/brand.py` is drawn in conversation
avatars; platform application icons are unchanged. Workspace defaults are
`Documents/Odessy` on Windows and `~/Odessy` on Linux/macOS, outside installed files.
Installed setup offers Browse to select a different project folder.
