# Live voice update verification — 16 November 2026

**Latest installed check:** the November 17 combined update is recorded [here](../verification/combined-update-2026-11-17.md). The November 16 evidence below remains historical.

Branch: `fix/live-voice-configuration`; source base `b9b955702c271a01d06bf92d79223761fdcf0af1`, with recorded dirty source hashes in `.qualification/live-voice-20261116/source.json`.

## Result

The complete isolated **in-place versioned upgrade passed**, including the 29 pinned cached speech wheels (73 total dependency wheels), configuration preservation and owned-file verification. The earlier isolated Python upgrade also preserved profile fixtures. No uninstall or download was used. The later GUI upgrade of `D:\Odessy-test` completed; see the final receipt below.

The upgraded installed Python service passed authenticated HTTP voice reload, rejected unauthenticated requests and stale fingerprints, and retained its service instance identity. Native audio imports, microphone start/cancel, local TTS playback, and wake/VAD model construction passed using saved devices and cached assets. Spoken wake detection and STT recognition require user speech and remain human checks.

## Update the existing installation

1. Exit the main Odessy window to release its microphone and coordinator.
2. Run `.qualification/live-voice-20261116/Update existing Odessy.cmd` from this checkout. It opens the new candidate's maintenance page with the existing root `D:\Odessy-test` and verified complete payload already selected.
3. Choose **Repair or update files**, review the existing root and operation, then confirm. This creates a new owned version and preserves configuration/profile data and external speech models; no uninstall or fresh onboarding is required.
4. Wait for installation and Windows registration to finish, then choose Start/open Odessy. The old version remains retained for rollback.
5. Change an audio device in Settings and save. Idle audio should apply without restarting; stop recording/playback/wake listening before applying deferred changes. Test microphone, TTS and wake with your voice.

This is the existing installer maintenance workflow, not an automatic download/update-discovery service. The prepared launcher is specific to this checkout and test root. Do not distribute it as a general updater.

## Artifacts and limits

- Complete payload SHA-256: `ddf0dc40b612fcaef6603f76e72d2b01b040ab0c084fa465b9540f6cbe1297a5`.
- Complete developer NSIS installer SHA-256: `5014e85bce373912ac42d7b23ae662edbfa446493145daabbf7031f0043e263a`.
- The NSIS wrapper uses development-only zlib compression over the same payload. It was built, while the isolated upgrade was exercised through the versioned installer engine rather than clicking the native wizard.
- Artifact audit verified inventory/hashes; two speech packages have ambiguous wheel license metadata (`kokoro-onnx`, `espeakng-loader`). Their declared pinned licenses/notices remain in the candidate; publisher license acceptance and signing remain separate release gates.
- Source receipts: 63 backend, 18 Flutter and 3 Dart client checks; desktop analysis and release build passed in the preceding implementation. This continuation verified actual installed service/audio behavior.
- No commit, push or merge. `D:\Odessy` was untouched. `D:\Odessy-test` was updated through the corrected maintenance UI; configuration/profile/model preservation was checked.

Evidence and exact receipts: `.qualification/live-voice-20261116/finish-voice-package.log`, `full-install-result.json`, `installed-http-result.json`, `installed-audio-result.json`, `voice-complete-audit.json`.

The corrected candidate maintenance UI was driven through review, confirmation and completion using computer use. The updated installed application was launched from its Start Odessy action.

## GUI payload defect and correction — 22:43 CST
The previous GUI repair selected the old cached payload and was cancelled before activation; the original active version remains 4318f8c60b344835a2dad6258e524234. The manage-mode payload forwarding is corrected. A real SetupApplication regression failed with old-payload before the fix and passed afterward; 18 focused tests pass. Repair now polls its current journal and shows stage/elapsed time. The native rebuild passed and replaced the extracted candidate desktop; the ZIP and NSIS hashes above describe the earlier artifacts and must not be treated as the repaired GUI candidate. The actual GUI upgrade is still pending. Do not use the earlier ZIP/NSIS as proof of this GUI fix. No repeated upgrade was started in this correction window.
## Update-performance finding

The current Repair/update path constructs a complete replacement virtual environment, installs all packaged wheels, copies selected runtime components, hashes owned files, and prepares an external maintenance copy. It preserves user data and retains the previous version, but it is not a delta updater. The real user GUI attempt exceeded five minutes before finishing component copies. This is a contributor/product performance limitation, not evidence that the voice configuration fix failed. No downloads, uninstall or fresh onboarding were required for this attempt. Incremental/versioned reuse is future work; do not call the present operation a quick update or promise a percentage/ETA that its engine does not supply.

## Final GUI upgrade receipt — 16 November 2026

**PASS for the in-place GUI upgrade and installed audio smoke checks.** This is a full replacement upgrade, not an incremental updater or an automatic update-discovery service.

- Original active version: `4318f8c60b344835a2dad6258e524234`; new active version: `210d2ff0779f438e96e8380b1683db18`. The original version is retained.
- Real corrected maintenance GUI: Review → Confirm once → activation → Windows registration → Start Odessy. The journal proved dependencies came from the new 73-wheel candidate, not the old maintenance cache.
- Actual engine duration: 563.09 s; maintenance registration: 255.17 s; total from engine start through registration: 818.57 s (13m39s). Review/startup and later verification are additional time. Update performance is an open defect.
- Configuration bytes, both pre-existing profile files, and all five referenced speech assets retained their SHA-256 hashes. No uninstall, onboarding repetition, model download, or alteration of `D:\Odessy` occurred.
- Installed desktop EXE and `desktop/data/app.so` match the corrected extracted candidate. Installed changed Python modules match the packaged wheel. Voice imports pass. The refreshed external maintenance manifest matches the candidate.
- Actual upgraded interpreter: microphone start/cancel, local TTS playback, and wake/VAD model construction passed. No recording was saved. Spoken STT and wake-phrase recognition remain pending human tests.
- Actual installed app launched from `D:\Odessy-test\versions\210d2ff0779f438e96e8380b1683db18\desktop\odessy_desktop.exe`, rendered CONNECTED and enabled the main composer microphone. No paid model request was sent by these checks.
- The repair stage label still lags during maintenance-cache registration. Do not treat its last configuration label as the current copy stage. This remaining progress presentation defect does not change the completed registration receipt.
- Corrected extracted candidate provenance: `.qualification/live-voice-20261116/updater-fix-source.json`; earlier ZIP/NSIS binaries do not contain the subsequent Dart maintenance fix and must be rebuilt before distribution. No publication/commit/push is claimed.

Receipts: `gui-user-before.json`, `gui-user-update-result.json`, `user-installed-audio-result.json`, `updater-fix-source.json`, `D:\Odessy-test\operation.json`, and `D:\Odessy-test\registration.json`. Local evidence is under `.qualification/live-voice-20261116/` and is not a portable download location.

### User voice check

Open the main composer's **Voice options** → **Test speech output**, then **Test speech input · no model call** and speak. Enable wake listening separately and say the configured wake phrase (Hey Jarvis for the saved bundled model). Stop wake listening before changing audio devices. Save a voice setting and confirm applied/deferred acknowledgment without restarting; deferred changes apply once audio is idle. Human spoken checks are still required even though the installed audio smoke check passed.

## Subsequent TTS optimization

The November 16 source optimization is described in [speech playback latency](../ui/voice-performance.md). It is newer than the installed GUI receipt above. Do not reuse the earlier installer hashes to claim delivery of that optimization. A new coherent update candidate and installed check remain necessary.


## Combined installed update — November 17, 2026

The later combined candidate has now been applied to `D:\Odessy-test`, including the TTS optimization and SSH/history/compact-inspector fixes. The earlier artifacts and timings above remain historical. See the [new installed update receipt](../verification/combined-update-2026-11-17.md) for exact identities, preservation checks and voice-test steps.
