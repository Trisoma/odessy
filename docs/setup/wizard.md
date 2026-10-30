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

## Management

`--manage` (and **Settings → Manage installation** in the installed app) shows the
recorded state and offers: check installed files, repair or update from the bundled
payload, roll back to an owned version, change configuration, service controls,
repair the Start-menu entry and uninstall. Every mutation requires an exact review.

- Internal paths (Python, wheelhouse, payload) are never typed by the user. The
  installation records the owned interpreter and the cached maintenance payload.
- Management keeps working after the downloaded installer is deleted because the
  verified payload is cached outside the installation, under
  `%LOCALAPPDATA%\OdessyMaintenance\<id>\<hash>` on Windows and
  `${XDG_DATA_HOME:-~/.local/share}/odessy-maintenance/<id>/<hash>` on Linux. The
  cache sits outside the installation so uninstall can run from it.
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
  **Repair Start-menu entry** rewrites both. On Linux the launcher is written before
  the application-menu entry for the same reason, and the management button reads
  **Repair application-menu entry**.

## Per-user registration

Setup never elevates. It registers the installed copy for the current user only, and
the same ownership rules apply on both platforms.

| Platform | Launcher | Menu surface | Removal record |
|---|---|---|---|
| Windows | `launch.ps1` in the installation | Start-menu shortcut `Odessy-<id>.lnk` | `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Odessy-<id>` |
| Linux | `launch.sh` in the installation | `~/.local/share/applications/odessy-<id>.desktop`, with Manage and Uninstall actions | the same `.desktop` entry |

On Linux the launcher reads the active version from `installation.json`, so a rollback
only needs the entry refreshed rather than registered again. Both registered files are
recorded with digests; uninstall removes them only when they are still unchanged.

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

## Run from a distributed folder

`tool/linux/package_setup_app.sh` assembles a folder a normal user can install from:

```
OdessySetup-linux-x86_64/
  OdessySetup     the desktop binary, renamed from odessy_desktop
  lib/ data/      the Flutter bundle
  payload/        the unpacked offline payload, including setup-context.json
  install.sh      exec "$here/OdessySetup" "$@"
  README.txt
```

Running `./install.sh`, or a bare double-click on `OdessySetup`, installs with no
arguments, no path, and no system Python. The payload beside the executable decides
the interpreter (`payload/runtime/bin/python3`), the modules (`payload/bootstrap`)
and the window (`Odessy Setup`, 900x680). An explicit `--payload` or an explicit
mode still wins, and a copy without a sibling payload is untouched.

### Build order, and the cached copy

The menu entry and the previous installation keep their own verified copy of the
payload, under `~/.local/share/odessy-maintenance/<id>/<digest>/`, so management
and uninstall never depend on the folder that was used to install. That copy holds
the desktop binary too, which means the steps must run in order:

1. `flutter build linux --release` for the bundle,
2. `tool/linux/build_setup_payload.sh` to embed that bundle in the payload,
3. `tool/linux/package_setup_app.sh` to assemble the distribution folder,
4. install again from that folder.

Rebuilding the desktop without reinstalling leaves the cached copy one build
behind, so a Manage or Uninstall window started from the menu entry shows the old
behaviour. `strings <cache>/desktop/odessy_desktop | grep 'renderer probe'` says
which build a cached copy is.

### Software rendering

Impeller draws through OpenGL ES and produces incomplete frames on graphics
stacks without working acceleration: the window opens, its header paints and the
rest stays blank. The Linux runner reads `GL_RENDERER` through a 1x1 EGL pbuffer
before it creates the view, and switches to Skia when the driver reports a
software rasteriser (`llvmpipe`, `softpipe`, `swrast`, VMware `SVGA3D`). The
decision is logged once on stderr:

```
odessy: renderer 'svga3d; build: release;  llvm;'; using Skia
```

`ODESSY_DISABLE_IMPELLER=1` still forces Skia for anything the probe cannot see.
The probe is compiled only when `libepoxy-dev` is available; without it the
runner behaves as before and Impeller stays on.

### Reinstalling into the same folder

Uninstalling removes every file it recorded, then keeps the installation record
with `active: null` so management can still explain what happened. Such a folder
is reported as `new` to the installer rather than as an existing installation, so
installing into it again works without deleting anything by hand; the record and
its identity are reused for the new version. Files the installer never recorded
(and anything the user added) are left in place and are not counted as owned.

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
