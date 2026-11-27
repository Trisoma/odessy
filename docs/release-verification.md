# Release verification

**Developer Preview · v0.5.0-alpha**

Prepare one coherent candidate; do not combine older binaries with newer source.

1. Record commit, dirty paths/source hashes, platform, architecture, dependency
   locks, package inventory and optional helper/native closure.
2. Run affected Python, Dart/Flutter, Node, Rust and Go checks using the existing
   workflows. Record failures/skips and whether a test is a fixture or native run.
3. Build Python, desktop, helper and installer artifacts from that same source;
   audit hashes, ABI, required resources and licenses with existing release tools.
4. Use an isolated install root/profile for install/update/maintenance checks.
   Preserve settings, history, user files and credential/grant references. Never
   substitute fixture credentials for real secure-vault qualification.
   On Windows, keep system/profile variables such as `ProgramData` and
   `ALLUSERSPROFILE` when isolating user paths; a reduced launcher environment
   can break OpenSSH before authentication. Check the actual packaged launch.
   For optional Piper, verify the installed interpreter loads the model and
   produces audio; a cached-download marker is insufficient. Exercise managed
   repair/upgrade with preserved device selections and the explicit
   [missing-cache recovery](setup/voice-components.md#upgrading-after-removing-the-speech-cache).
5. Verify authenticated production connection, required Custom continuation,
   read-only Harness restart history, stale/revoked approvals and helper shutdown.
6. Verify Windows restoration works through the handle-anchored replacement and
   matches the POSIX safety contract: creation/list/preview/restore all function,
   a path component that is a junction or symlink is refused before any write, and
   the replaced file is readable afterwards. The candidate must bundle the same
   `win32_replace` module as the tested commit; a build that falls back to a plain
   path-based restore is not acceptable. On a host where the NT bindings are
   unavailable, restore must return HTTP 501 with no approval or file writes —
   verify whichever branch applies and record the host in the evidence.
7. Record matching screenshots/native executable identity, then obtain human
   visual acceptance. Signing, hosted CI, provider/hardware evidence and publication
   are separately recorded, never implied by a local passing test.

Existing entry points: `scripts/build_setup_bundle.py`,
`scripts/audit_setup_artifact.py`, `scripts/package_native_setup.py` and
`scripts/windows_nsis.py`. Read their `--help` and [installer guide](../installer/README.md)
for exact build inputs; do not run a historical maintainer-specific command blindly.

The Native setup acceptance workflow uploads a `.provenance.json` beside each
ZIP. It records the checkout revision, dependency input hashes, ZIP hash and
payload file hashes. The workflow verifies the ZIP contents before uploading.
For the public Windows installer, record the inputs and hashes for the exact
installer candidate separately.

The [gallery manifest](view/verification/manifest.json) describes its recorded
snapshot, not every later commit. Earlier installed receipts remain useful
evidence but cannot certify a candidate rebuilt after merge/review fixes.
