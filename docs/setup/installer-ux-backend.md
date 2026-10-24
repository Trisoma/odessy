# Installer UX backend handoff - October 24

This checkpoint covers Python/native packaging only. DeepSeek owns the concurrent
Flutter changes. Do not treat a native build or backend tests as final GUI acceptance.
The shared interface is [installer-ux-contract.md](installer-ux-contract.md).

## Implemented

- Additive progress status: cursor-based bounded redacted logs, operation/stage
  timing, durable boundaries, throttled progress-only updates and hidden child
  consoles. Overlong child output lines are omitted in full before redaction;
  cursor polling does not duplicate the log buffer inside the operation journal.
- Existing cancellation, ownership, rollback confirmation and conservative helper
  retention remain in force. Log redaction is best effort, not a general guarantee
  that arbitrary third-party output cannot contain secrets.
- Direct Windows uninstall intent opens review; Modify opens management. Commands
  are constructed as arguments, not by replacing substrings in installation paths.
- Version-bound launch IDs and ready/configuration_required/failed receipts let
  the UI close setup after acknowledgement, not merely after process creation.
  Receipt pruning preserves unowned files. This handoff does not grant permissions.
- Windows packaging now uses NSIS Unicode, with one compression/extraction layer,
  a native Preparing progress screen, per-attempt staging, a branded icon and no
  CMD/PowerShell bootstrap. Partial staging remains inspectable. No downloaded EXE
  deletion, recursive helper cleanup or delayed self-deletion is introduced.
- The Windows runner icon resource now uses the existing Odessy boat artwork.

## Build requirements and commands

NSIS is a build dependency, not a runtime dependency. Set `ODESSY_MAKENSIS` to a
trusted Unicode makensis.exe or put it on PATH. The workspace-local fallback is
`../.tooling/nsis-3.13/nsis-3.13/makensis.exe`. The verified portable tool archive
used here has SHA-256
`ba63dffc4410ee89193e1cb5a41989991bd77c61068da17e3156d136b7b0b3d8`.
Provenance: [official NSIS project download](https://sourceforge.net/projects/nsis/files/NSIS%203/3.13/nsis-3.13.zip/download).
Review its `COPYING` file when preparing redistributed notices.

```powershell
# First rebuild the wheel, Flutter release, payload and reviewed dependency inventory
# from final sources. Then package the audited payload:
python tool/package_native_setup.py --payload <payload.zip> --platform windows --output <new-OdessySetup.exe>
python tool/sign_setup_artifact.py --artifact <new-OdessySetup.exe> --platform windows --identity <certificate-thumbprint> --timestamp <HTTPS-timestamp-service>
```

Do not pass `--script-signing-thumbprint` for the new Windows wrapper: it has no
PowerShell bootstrap script. Sign the final native artifact separately. Linux and
macOS wrappers are unchanged; no new native qualification is implied.

Before compiling, the builder audits payload hashes and rejects Windows path
aliases/case collisions, traversal, links and oversized archives. The native EXE
uses NSIS CRC checking. Neither manifests nor CRC replace publisher signing.

## Measurements and actual artifact

The recorded baseline used the existing 67,777,815-byte payload (1,305 members;
180,229,610 uncompressed bytes). One local run measured 1,305 ms for full archive
audit and 8,240 ms for ZIP extraction. This excludes outer IExpress extraction and
Flutter startup; it is not a cold/warm median or time-to-first-visible-window result.

A real NSIS compile of that same baseline payload succeeded:
`../.qualification/installer-ux-baseline/OdessySetup-nsis-baseline.exe`.
It is 48,810,315 bytes, versus 67,354,624 bytes for the old IExpress artifact.
It contains the **older baseline application**, not DeepSeek's current UI and not
the latest Python UX additions. Do not distribute it as the final optimized build.
Measurement records are in `../.qualification/installer-ux-baseline/baseline.json`.

## Checks still required after Flutter handoff

October 24 deferred-configuration follow-up: preparation and package-review
timings appear immediately on the install page because those calls already
finished on earlier wizard pages; explanatory text now states this explicitly.
The first-run footer's Configure later action defers model setup in place rather
than closing the app. Configure a model returns to model selection; the separate
Close action remains the exit path. Deferral performs no configuration mutation,
provider contact or inference. A footer-specific regression covers staying open
and returning to model selection.

October 24 screenshot follow-up: completed UI stages retain their finish timestamp;
engine stage-event timestamps reconstruct phases missed between polls, and the
last snapshot is read before finishing. Preparation/review durations are retained
from their actual controller calls. Runtime timing begins before portable-runtime
copying. Checklist rows open model/endpoint controls, the workspace picker, or
explicit optional-voice guidance (voice setup is not implemented by that page).
Readiness acknowledgement waits for the shared helper's initial management
inspection and a rendered frame. Verified with 97 Flutter tests (4 skipped),
29 affected Python tests, clean Flutter analysis and a Windows release build.
A real native Flutter window and Python bridge against an isolated no-model
installation fixture produced `configuration_required`; this is not a complete
mouse-driven install/uninstall acceptance run.

October 24 bootstrap correction: progress logging imports the shared
`odessy.authority.redaction` module before the application wheel is installed.
The bundle builder now includes that module and its package initializer in the
bootstrap. An isolated extracted-bundle subprocess regression reproduces the
previous missing-module error and verifies stage logging and secret redaction
without relying on the checkout or an installed application. Rebuild the payload
and native EXE; repackaging an old payload does not include this correction.

1. Confirm DeepSeek has stopped editing, then reconcile the typed contract and
   check uninstall/receipt intents and all UI states without default demo fallback.
2. Run final Python, Dart, Flutter, architecture/static checks and Windows release
   build; rebuild and audit the wheel/payload/native artifact from those sources.
3. Exercise the actual native EXE and installed GUI: picker, redacted live output,
   install, acknowledged launch/close, settings and direct reviewed uninstall.
4. Measure cold/warm bootstrap first-visible and interactive times on the same
   machine. No 30% launch-speed improvement is established at this checkpoint.
5. Verify duplicate-window handling and native picker accessibility with the real
   application. Publisher signing and physical model/voice tests remain separate.
