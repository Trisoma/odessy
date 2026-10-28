# Setup wizard and installed launch

`OdessySetup.exe` extracts its verified payload and opens the dedicated setup
window (`--setup`). The same desktop binary opens installed management with
`--manage`, and the Start-menu entry starts the installed copy with `--installed`.

The setup window has six steps: welcome, this computer, model and voice, review,
install and finish. Setup never downloads a model, starts inference or requests
administrator rights; it installs the packaged components into a folder the user
chooses and records what it owns.

## Packaged flow

```text
OdessySetup.exe
  └─ extract payload → desktop --setup            (no model, no service)
       ├─ review   → exact plan identity bound to role/paths/components
       ├─ install  → owned version + active configuration + Start-menu entry
       └─ finish   → Open Odessy | Manage installation | Close
            └─ installed desktop --installed --install-root <root>
                 ├─ validates the active executable hashes and configuration
                 ├─ model configured → starts the owned coordinator
                 │    (`python -m odessy.setup.installed`, per-launch bearer token,
                 │     loopback only, exits when the app closes its stdin)
                 └─ model not configured → opens configuration/management,
                      no coordinator and no inference
```

- **Existing installation.** Choosing a folder that already holds a versioned
  installation shows management instead of a second install; the plan review also
  refuses to write into it.
- **Model.** An explicit, bounded Ollama inventory request lists installed models.
  Unreachable and empty answers are distinct and never trigger a download. A
  compatible remote endpoint needs an endpoint and model name; credentials are
  referenced by environment-variable name only.
- **Voice.** Optional and unavailable until all four existing asset paths
  (Whisper.cpp executable, Whisper model, Kokoro model, Kokoro voices) are
  supplied; otherwise the wizard continues text-only.
- **Review.** A human-readable summary (role, location, model, voice, downloads,
  service) with expandable technical details. Editing any option invalidates the
  review; Install is disabled until a fresh review exists.
- **Progress.** Installation progress is read from the durable operation journal
  by a second read-only helper (stage, packaged steps, files written). Cancelling
  stops future steps and leaves the previous active version untouched.
- **Finish.** *Open Odessy* starts the installed application; *Manage installation*
  opens maintenance for this installation; *Close* ends the setup process.
- **Configure later.** With no model configured, the installed application opens
  configuration and management directly. Nothing is inferred or downloaded until
  the user selects an existing model.

## Window sizing

The native runner chooses the window from the flags it was started with, not from
the window title or the first screen drawn:

- `--setup`, `--manage`, `--uninstall`: one compact installer surface, 900 × 680
  logical pixels, resizable down to 720 × 540.
- `--installed` and the developer entry point without a mode flag: the main
  application keeps 1200 × 850 logical pixels.

The initial compact size is reduced to the primary monitor's work area when the
display is small or scaled to 150–200%, so the fixed footer and its primary
action stay on screen. The minimum is reduced with it and never exceeds the
initial size; the wizard body scrolls, so no control is clipped at the minimum.
Windows only in v0.5.0; Linux and macOS runners change with their own platform
PRs.

## Management

`--manage` (and **Settings → Manage installation** in the installed app) shows the
recorded state and offers: check installed files, repair or update from the bundled
payload, roll back to an owned version, change configuration, service controls,
repair the Start-menu entry and uninstall. Every mutation requires an exact review.

- Internal paths (Python, wheelhouse, payload) are never typed by the user. The
  installation records the owned interpreter and the cached maintenance payload.
- Management keeps working after the downloaded installer is deleted because the
  verified payload is cached under `%LOCALAPPDATA%\OdessyMaintenance\<id>\<hash>`.
- Uninstall runs from that external cache: a running installation cannot remove the
  files it executes. The installed app offers **Open maintenance to uninstall**
  instead of removing itself.
- Only recorded owned files are removed. External models, credentials, settings and
  unrelated files are preserved. Cache cleanup removes only unchanged files from
  its recorded ownership inventory. Changed/unowned contents, older caches without
  that inventory, and a cache running the current helper are retained explicitly.
  No recursive cleanup or delayed self-deletion is scheduled.
- A failed Installed-apps registry write is recorded as a partial registration. The
  Start-menu shortcut is written first, so the installation stays reachable and
  **Repair Start-menu entry** rewrites both.

## Run from a checkout

From `apps/desktop`, use the Flutter SDK with a Python environment containing
Odessy. If `python -m odessy` reports a missing module, point the helper explicitly
at the repository and its interpreter:

```powershell
flutter run -d windows --dart-define=ODESSY_SETUP_PYTHON=C:/path/odessy/.venv/Scripts/python.exe --dart-define=ODESSY_SETUP_ROOT=C:/path/odessy
```

On Linux/macOS use the same defines with your Python and checkout paths and the
corresponding desktop device. These are build-time defines, not shell variables.
The default helper is `python -m odessy.setup.bridge`. The wizard works without a
running coordinator API or token; its setup helper is local even in demo mode.

## Behavior

- Combined: local coordination and execution. Coordinator: model/voice with
  separately authorized execution nodes. Advanced execution node: no model or voice.
- Model detection is an explicit, bounded Ollama inventory request. It performs
  no inference or model pull. Select any compatible installed model or leave it
  unconfigured. Cloud credentials stay in environment references, outside forms.
- Voice is optional. Diagnostics check existing executables/assets and missing
  configuration; presence is not physical audio or model compatibility acceptance.
- Cancel stops the owned helper and discards late replies. An interrupted save may
  have completed its atomic write: **Inspect saved state** reads the destination
  without repeating save. No configuration journal or credentials are stored by the UI.
- Diagnostics, discovery and review can be retried. Mutating operations are never
  automatically retried. Shell commands and arbitrary executables are not accepted
  through the protocol; only the configured Python and fixed module are launched.

The helper limits requests to 64 KiB, stdout to 4 MiB and stderr to 64 KiB.
Response waiting has a 30-second timeout (12 minutes for mutating management);
OS process creation is outside that response timeout. Error replies carry a
bounded, sanitized `reason`; raw stdout and environment values are never returned.

## Checks

```powershell
python -m pytest tests/test_setup_bridge.py tests/test_setup_configuration.py tests/test_setup_installed.py
cd apps/desktop
$env:ODESSY_TEST_PYTHON = 'C:/path/odessy/.venv/Scripts/python.exe'
flutter test --no-pub
flutter analyze --no-pub
```

The opt-in native integration tests execute the real Python helper: create-only
save/recovery, a cancelled stalled inventory, legacy maintenance, and an installed
`installed-spec` review plus configuration change. Without `ODESSY_TEST_PYTHON`
those integration tests are explicitly skipped.

## Native lifecycle continuation

Headless offline bootstrap, per-user service commands and conservative
repair/rollback/uninstall are also available outside the GUI. See
[native lifecycle](native-lifecycle.md). Windows is the only packaged platform in
this repository; Linux/macOS builds, OS service permission and real-device
acceptance remain qualification gates.
