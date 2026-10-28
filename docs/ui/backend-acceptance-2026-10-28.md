# Desktop backend and Flutter handoff verification — October 28, 2026

## Implemented software

- Provider checks distinguish configured, missing and explicitly tested. Model
  probes use one tools-disabled exchange, no repair/failover, the existing API
  ledger/egress and explicit consent. Each helper is a separate diagnostic run,
  not the coordinator's budget. Results bind to displayed values and include
  fingerprint, provider/model, time and usage when reported. Referenced opaque
  credentials are redacted as well as recognized token patterns.
- STT microphone/WAV and TTS playback tests work without an LLM/coordinator.
  Microphone recording is bounded to five seconds; test WAVs/playback are bounded
  to thirty seconds. Cancellation closes owned resources, including cancellation
  during provider/device initialization and immediately before playback admission.
- Native PortAudio device enumeration/selection uses host API plus device name;
  missing/ambiguous devices fail. Linux retains its existing command backend.
  An OS-backed microphone lease prevents diagnostic/coordinator double capture.
  Stop during device startup retains ownership until startup drains.
- Desktop push-to-talk/wake reuse the current controller, history and interaction
  broker. Exact positive approval and stronger-channel gates remain authoritative.
  Turning wake off preserves pending UI answers; stopping playback stops wake
  speech while retaining the listener. Audio errors never replay model/tool work.
- Pairing, independent execution/telemetry grants, registry revision revalidation
  and revocation reuse Rust/native operations. Trust is distinct from connectivity.
  Flutter handoff was reviewed against real approval payloads: `operation` and
  `target_identity` now populate the authoritative action/identity rows.
- Saved/running fingerprints, reviewed restart and interrupted settings recovery
  remain intact. The settings inventory now includes all 105 declared settings.
- Disposable patch-job tests remove their own Git metadata after assertions,
  retaining debugging files without leaving editor-visible worktrees/branches.

## Verification evidence

- Affected Python runtime/API/provider/configuration/pairing suite: **223 passed**.
  Final cancellation/redaction/cleanup regression suite: **21 passed** (overlaps
  the affected suite; do not add these figures).
- Combined Flutter handoff: **153 passed, 7 skipped**; analysis **no issues**.
- Pure Dart client: **14 passed, 2 skipped**; analysis **no issues**.
- Windows release build: **passed**, 49.3 seconds. Binary:
  `apps/desktop/build/windows/x64/runner/Release/odessy_desktop.exe`.
- Rust pairing suite: previously verified **76 passed** with normal Windows user
  permissions; Rust sources were unchanged during this closeout.
- Independent review found the diagnostic stop-before-play generation race. A
  failing regression reproduced it; generation-bound playback fixes it.
- Local artifacts/reports: outer `.qualification/ux-backend-release/`, including
  `verified-wheelhouse/`, dependency wheels and installed `final-site/`.
  `verification.json` records outside-checkout package checks; wheel SHA-256 is
  adjacent to the final wheel. No credentials/model weights are bundled.

The earlier broad sandbox run reported 2269 passed, 27 skipped, 17 failures.
After correcting stale test fixtures, Python PATH and subprocess permissions,
the relevant integration rerun passed 136 tests. Four legacy Tk UI tests require
a Tk-enabled interpreter; the bundled qualification interpreter has no Tkinter.
This record does not describe that broad run as fully green or disable those tests.

## Acceptance boundary

Widget screenshots are rendered UI evidence, not native microphone or screen-reader
acceptance. Real paid providers were not called. Clean-machine distribution trials,
physical voice/echo/wake behavior, two-device pairing, OS service qualification,
assistive-technology trials and external-user acceptance require the corresponding
machines/accounts/users. Signing and final redistribution approval remain release
gates. A Windows build does not qualify Linux/macOS/ARM64. The implementation plan
retains these acceptance distinctions; this is software closeout, not a claim that
every release/platform gate is passed.
