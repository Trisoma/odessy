# Native setup acceptance — October 24, 2026

## Evidence from this pass

- TDD: missing service/maintenance modules and bundle builder reproduced first;
  independent review found rollback-selection and idle-task-removal defects.
  Both regressions failed before the fixes and then passed.
- Real Windows wheel built and installed outside the source checkout. Execution
  contract JSON imports, lightweight setup and lifecycle help all passed.
- Real Windows offline virtualenv install, repair, rollback, hash verification and
  safe uninstall passed using native dependency wheels. External fixture/settings
  remained. No provider/model inference or paid API was involved.
- Kernel lock, cancellation, checksum failures, stale consent, failed upgrade,
  changed-file retention and external service preservation have regression coverage.
- Windows native logon-task registration was attempted with a unique harmless
  temporary task; the OS returned `Access is denied`. No elevation was attempted,
  no task was registered, and the owned failed-attempt record remains under the
  ignored local qualification directory for inspection.
- Previously authorized Jetson SSH timed out at 10.1.1.2. No device was changed.

## Not accepted yet

- Successful native service registration/start/stop/remove on Windows and native
  systemd/LaunchAgent behavior on Ubuntu/macOS. Fake-runner tests verify generated
  definitions and ownership gates, not OS execution.
- Linux/macOS/ARM64 artifacts until their new CI runs and target-native acceptance
  pass. CI definitions themselves are not test results.
- Signed standalone native installers, bundled Python/Node/Rust bootstrap, GUI
  maintenance controls, automatic partial-directory cleanup or resume.
- Actual device voice, coordinator-to-node pairing/revocation and packaged-device
  tests. They cannot be inferred from the offline runtime test.
- Final redistributed dependency notices and package version alignment. Manifest
  hashes are integrity checks, not a replacement for authenticated distribution.

The implementation is deliberately fail-closed where a dependency, native user
manager or authority grant is unavailable. Do not label the entire installation
plan complete on the evidence above.


Final regression command: `pytest tests/test_setup*.py tests/test_installer*.py -q`
(expanded as actual filenames in PowerShell): **430 passed, 1 skipped, 101 subtests
passed**. Windows offline archive built, CRC verified and extracted bootstrap help
passed. No commit, merge, push or automatic privilege escalation was performed.
