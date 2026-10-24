# Setup completion handoff — October 24, 2026

## Implemented in this continuation

Flutter now has Runtime maintenance and verified Pair a device workflows, using
protected controllers and the bounded local helper. Install/upgrade, repair,
rollback, integrity, service start/stop/status/registration/removal and uninstall
use explicit review and confirmation. Pairing reuses Rust initialization, public
identity export, discovery, enrollment, local grants and revocation. Enrollment
requires an independently verified fingerprint; grant review binds certificate,
binary and the current peer/permission snapshot. No model approves anything.

Standalone payloads can include portable CPython and the desktop. Python is copied
to a persistent owned location before creating the installed environment; external
site-packages are excluded. Native packaging wrappers produce Windows EXE,
macOS DMG and Linux tar archives on the appropriate OS. Signing hooks use external
certificate/keychain identities; keys never enter setup settings. Development
Python and project metadata now agree on `0.5.0.dev0`.

## Actual verification

- Broad setup/legacy installer regression run: 435 passed, 2 skipped,
  101 subtests passed; subsequent focused checks cover final review corrections.
- Final Flutter suite: 40 passed, including real local helper integration.
- Pure Dart client: 9 passed, 2 existing opt-in integrations skipped; analysis clean.
- Real Rust temporary identity enrollment, workspace grant and revocation through
  the GUI bridge passed. This is local integration, not a second physical host.
- Actual bundled Python launched without system Python. Real persistent-runtime
  install, verify and safe uninstall passed.
- A development Windows EXE was produced using IExpress. It is unsigned and not a
  release artifact. Rebuild it from the final reviewed desktop/wheel before use;
  the first EXE build preceded final repair/runtime-discovery corrections.
- The artifact inventory verifies wheel hashes, Python license and Flutter notices.
  Missing license metadata remains explicitly unknown; no legal clearance claimed.

## Yours to finish

### 1. Build/sign the exact final artifacts

Build the final wheel with `python -m pip wheel . --no-build-isolation --no-deps -w <wheelhouse>`.
Collect matching dependency/extra wheels for the target OS. Review a filename-to-
license map; retain all wheel licenses and CPython/Flutter/native component notices.

```text
python tool/build_setup_bundle.py --wheelhouse <wheelhouse> --licenses <licenses.json> --python-runtime <portable-CPython-prefix> --desktop-bundle <Flutter-release-directory> --output <payload.zip>
python tool/audit_setup_artifact.py <payload.zip>
python tool/package_native_setup.py --payload <payload.zip> --platform windows --output <OdessySetup.exe>
python tool/sign_setup_artifact.py --artifact <OdessySetup.exe> --platform windows --identity <certificate-thumbprint> --timestamp <HTTPS-timestamp-service>
```

Windows needs NSIS at build time and a trusted code-signing certificate plus SignTool
for publication. The native wrapper has no PowerShell bootstrap to sign; sign/verify
the final EXE. See [backend UX handoff](installer-ux-backend.md). Developer unsigned
builds may be blocked by OS policy and must not be advertised as trusted releases.
For macOS build on the target architecture, sign the app/runtime binaries using your
Developer ID, package with `--platform macos`, and notarize/staple using your existing
keychain profile. Linux uses `--platform linux` and an explicit GPG signing identity.
Never paste private keys or signing passwords into chat.

### 2. Native service qualification

On Windows with appropriate Task Scheduler permission, Ubuntu with a running user
systemd session, and macOS with an interactive user LaunchAgent session:

```text
python tool/check_setup_services.py --accept-native-user-service --report-dir <new-report-directory>
```

This explicitly registers a harmless short-lived user task, exercises lifecycle
and attempts cleanup. Inspect `acceptance.json`; preserve/resolve any reported
cleanup requirement. Windows here returned Access is denied; no elevation or
successful registration was claimed. Do not work around organizational policy.

### 3. Real-device acceptance

Reconnect Jetson/network/SSH, then run the chosen platform's installed package and
verify text-only, existing local model, optional voice and headless roles. Run the
existing verified Rust pairing flow between coordinator and execution node, grant
only the intended workspace on the node, perform a harmless scoped read, revoke,
and confirm the next dispatch is denied. Check disconnect/cancellation without
replay. Finish deployed echo/pre-cue and audio smoke checks with the actual device.
The last Jetson connection timed out; no remote or physical test was fabricated.

Linux/macOS native builds, signatures/notarization, successful OS service checks,
exact final release artifact/license acceptance and physical hardware tests remain
owner gates. Source features are not substitutes for that evidence.

Final focused lifecycle/pairing/version/eval regression check: **91 passed**. Final Flutter analysis is clean; protected desktop settings contract validates 89 settings.

## October 24 maintenance integration update

The desktop maintenance controls now handle both owned installation formats through
their original engines, including legacy repair/recovery/uninstall. They support
explicit post-cancellation inspection and fresh review; they do not silently resume
or remove partial directories. See [integration evidence](existing-installer-wiring.md).
This supersedes earlier GUI-maintenance gaps, but does not discharge signatures,
target-native service checks, Linux/macOS component support or physical acceptance.

## October 24 installed-launch and management continuation

The user-facing flow is now closed end to end: `OdessySetup.exe` → dedicated wizard
→ install → Start-menu launch of the installed copy.

- `--installed` is a real entrypoint. It validates the active version's executable
  hashes and the recorded configuration, then starts the owned coordinator with a
  per-launch bearer token on loopback and stops it when the application closes.
  There is no demo fallback and no implicit model selection or download.
- With no model configured (or on an execution node), the installed app opens
  configuration and management instead of starting inference.
- The wizard adds bounded existing-model discovery, the four optional voice asset
  paths (voice stays unavailable until complete), a readable review with expandable
  diagnostics, journal-derived progress, and Finish → Open / Manage / Close.
- Installed management replaces the technical maintenance page in `--manage` mode:
  check files, repair/update from the cached payload, roll back, change
  configuration, service controls, repair the Start-menu entry and uninstall —
  without typing any internal path.
- Registration coordination: the shortcut targets the active owned version and is
  rewritten after activation changes; a failed Installed-apps write leaves a
  recorded partial state plus a working shortcut to repair from.
- Uninstall runs only from the external maintenance cache; the installed app
  delegates to that window. Only recorded owned files are removed.

See [wizard and installed launch](wizard.md).
