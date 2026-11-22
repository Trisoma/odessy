# Installed combined update — November 17, 2026

**Developer Preview · v0.5.0-alpha**

Updated the existing `D:\Odessy-test` installation using the existing versioned
installer engine and refreshed its Windows shortcut/maintenance payload. No
uninstall, repeated onboarding, dependency/model download or change to
`D:\Odessy` was performed. This is a full replacement upgrade, not a delta
updater or automatic update discovery. The native wizard was not driven for this
run; the same packaged installation/registration owners were called directly.

## What shipped

- Live voice settings and bounded phrase TTS with one-phrase synthesis lookahead,
  streaming model sentences when supported, and automatic compatible accelerator
  selection with CPU fallback.
- Direct SSH computer input, session-only passwords and honest Git-only endpoint
  diagnosis. SSH remains monitoring-only, with execution/workspace capabilities
  unavailable. Jetson hardware qualification remains pending.
- Direct saved-chat opening with right-click/overflow management, and the narrow
  inspector dock that retains the conversation/composer.

## Verified installation

| Check | Result |
| --- | --- |
| Isolated existing-installation upgrade | Passed; previous version and profile/configuration retained. |
| Isolated installed authenticated HTTP | Voice configuration reload applied in the same service instance; stale fingerprint/unauthenticated requests refused; SSH routes present. |
| Existing user installation | `210d2ff0779f438e96e8380b1683db18` → `fb4f06126beb405eb495030bcae75a7a`; previous version retained. |
| Installed code identity | 233 Python source/resource files, desktop EXE/app.so and native monitor match the candidate. |
| Owned-file verification | Passed before launching the application. |
| Preserved data | Configuration bytes, 2 profile files, 5 referenced speech assets, 2 desktop preference files and existing SSH trust hashes matched before relaunch. |
| Native password helper | Installed loopback broker/helper read passed with a test credential; no live remote password was used. |
| Windows registration | Complete; shortcut points to the new active version and maintenance cache matches the new payload. |
| Native application launch | New installed executable acknowledged `ready`; launched in real installed mode, without `--demo`. |
| Downloads/publication | None. Changes remain uncommitted on `fix/ssh-history-inspector`. |

Installation and verification took 1035.9 seconds;
Windows maintenance registration took 372.2 seconds.
These exclude candidate preparation and isolated checks. The current update path
still builds a replacement environment and retains previous versions.

## Installed TTS measurement

Used the **new installed interpreter**, cached Kokoro model and a fixed synthetic
answer, with a silent playback sink. First software playback request:

- Whole answer: **79.430 s**.
- Bounded phrases: **22.463 s**
  (71.7% earlier).
- Total synthesis: 79.431 s versus
  91.449 s. This measures onset, not a CPU-throughput
  improvement; one alternating pair is not a controlled statistical benchmark.

The first real playback failed because the preserved saved output device
(`MME::耳机 (JBL Tune 770NC)`) is currently absent from Windows' device list.
No saved setting was changed. An explicitly temporary system-default output
then completed playback of “Odessy update complete.” This does not establish
playback through the unavailable headset. Reconnect it or select an available
output in Settings before testing main-panel voice. Microphone access was not
opened by this test. Human audibility,
spoken STT/wake-word recognition, intelligibility, live Jetson metrics and GPU
hardware acceleration remain separate checks. No paid model request was sent.

## Try the updated application

The updated installed app was opened for review. If closed, use its refreshed
Odessy shortcut or `D:\Odessy-test\launch.ps1`.

1. Reconnect the saved JBL Tune 770NC headphones, or choose an available output
   under **Settings → Voice** and save. Then in the main composer, open
   **Voice options → Test speech output**.
2. Select **Test speech input · no model call**, speak, then finish recording.
3. Enable wake listening separately and say the configured wake phrase (the saved
   bundled model uses **Hey Jarvis**).
4. Save a voice/device setting. Idle audio applies without restarting; stop an
   active recording/playback before a deferred configuration can apply.
5. Click an existing saved chat, open its right-click menu, and open the inspector
   at a narrow window width to check the usability fixes.

Do not rerun the update merely to test voice. For a future manual review using
this exact local candidate, `Review update for Odessy-test.cmd` under the evidence
directory opens maintenance with the candidate payload and root explicitly set;
review **Repair or update files** before confirming once. It is checkout-specific,
not a public download or automatic updater. Rollback remains available through
the existing maintenance workflow; no rollback was exercised on the user app.

## Artifact identity and limits

- Source base: `3b7cea13922d773f95275c970f1d50c275cd5a04` plus the hashed dirty source snapshot.
- Payload SHA-256: `e0841e72a700ebfd5f88d711e4d44011e40257458c8afe29499ebaa14bec0e57`.
- Wheel SHA-256: `7072f8edb1e94afb0d48e95d1db6239dc2a43a812dc548151496346981c37f5a`.
- Windows desktop app.so SHA-256: `b72f63f7d9783d238ba3c330574762d80851d478f5ae5c207f22434216ad2e07`.
- Native monitor SHA-256: `0136d01b3cb3aebd6911a2d24769cf9dee92e8a638b2734f0e691dd3e5ade634`.
- Pinned cached wheels: 73; original cached component bytes
  were reverified before reuse. Full dependency license expressions were taken
  from wheel metadata rather than old shortened labels. Existing ambiguous wheel
  metadata for Kokoro/espeak remains a separate publisher license-review gate.

Exact source hashes, install/registration timings, preservation hashes, package
audit and installed benchmark/launch receipts are retained locally under
`.qualification/combined-update-20261117/`. Current source validation is recorded
in the [SSH/history/inspector receipt](ssh-history-inspector.md); the earlier
[voice receipt](../setup/live-voice-update-verification.md) remains historical
evidence. These are local installed-preview checks, not signing, hosted CI,
clean-machine qualification, release publication or human acceptance.
