# Speech playback latency

**Developer Preview · v0.5.0-alpha**

## Piper migration decision — November 17, 2026

Piper is Odessy's selected TTS engine following the CPU evaluation, replacing
Kokoro. The [external Piper connector](piper-connection.md) now uses existing
provider/executable/model settings. Further Kokoro latency tuning is stopped.
Setup now offers optional automatic Piper engine/model installation and saves
the selected paths after verification; Kokoro remains selectable. See
[setup voice components](../setup/voice-components.md). Redistribution
qualification remains separate from synthesis measurements.

The standalone Piper medium voice generated roughly 12.4 seconds of audio in
0.38 seconds at eight CPU threads on the tested laptop; four threads took about
0.47 seconds. Sixteen threads was slower. This is synthesis evidence, not human
voice-quality approval or a production realtime claim. See the
[organized TTS benchmark and receipts](../../benchmark/tts/README.md),
[listening preview](../../benchmark/tts/license-review.md#try-it-without-changing-odessy)
and [benchmark index](../../benchmark/README.md).

Before bundled shipment: finish GPL/Corresponding Source and model/native license
review and qualify the selected resources. The external user-installed connector
retains existing Save/Stop/wake/STT ownership. The Kokoro measurements below
are retained historical evidence, not the selected engine or a recommendation
for new installations.

The main-panel spoken-turn path uses the existing runtime text stream and audio
lease. Complete sentences can play before model generation ends. A provider that
does not emit text deltas still speaks its completed answer. Tool actions retain
their existing authorization; speech never grants permission or replays a turn.

## How playback works

- Split completed output at sentence/phrase boundaries, normally up to 160
  characters. Individual words are not separate model inference requests.
- Synthesize one phrase ahead while playing the current phrase. Only two WAV
  files need to be owned at once; synthesis for one answer remains serial.
- Bound the desktop streaming queue to eight phrases. If generation outruns
  playback, stop queuing new phrases and speak the remaining final suffix once.
- Stop invalidates the audio epoch. Cancellation retains files until blocking
  synthesis/playback releases them, then removes them. Audio failure keeps the
  text answer available and never retries the agent turn.

Kokoro sessions remain cached. Automatic ONNX selection prefers a compatible
installed accelerator and retries CPU if initialization fails. Explicit GPU
selection reports errors. No GPU runtime, model or driver downloads are added.
The packaged CPU runtime cannot use an Intel GPU simply because one is present.

## Laptop measurement — November 16, 2026

Silent software playback requests using the existing installed Python 3.11
dependencies, Kokoro int8 model, CPU provider and a synthetic answer:

| Run | Whole-answer first playback | Phrase first playback | Whole synthesis | Phrase synthesis |
| --- | ---: | ---: | ---: | ---: |
| 1 | 24.144 s | 4.290 s | 24.145 s | 28.022 s |
| 2 | 22.407 s | 4.458 s | 22.408 s | 28.236 s |

First playback was about 81% earlier. Total synthesis was slower because short
requests add overhead; this change improves response onset, not CPU throughput.
The silent player adds no playback duration. These are two alternating runs on
one shared laptop, not controlled statistical or device-audibility measurements.
Model loading took 2.001 seconds separately. No GPU hardware was qualified.

The phrase-splitting and playback modules still match the benchmark's source
hashes. A later ONNX change adds the required DirectML session options; the
measurement used CPU and provides no DirectML performance evidence.

Local raw receipts and commands are under the ignored
`.qualification/live-voice-20261116/` directory: `benchmark_tts_optimization.py`,
`tts-optimization-result.json`, `tts-regression.log` and `tts-wheel-build.log`.
The benchmark imported working source with installed dependencies; it did not
replace the user's installed files. Wheel verification and source tests do not
qualify a complete updated installer. Older installed applications require a
new source-matched update candidate to receive this optimization.

## Offline regression checks

With the repository's supported test environment, run:

```sh
python -m pytest tests/test_voice_speech_latency.py tests/test_onnx_runtime_wiring.py tests/test_desktop_voice.py tests/test_desktop_voice_api.py tests/test_voice_runtime_events.py tests/test_hybrid_audio.py tests/test_live_voice_configuration.py -q
```

These use fixtures and temporary workspaces; no microphone, paid provider or GPU
is required. Real speech intelligibility, hardware acceleration and updated
installer delivery remain separate checks.

The earlier combined affected backend suite passed 410 tests with 2 skips;
26 focused tests passed after the DirectML adjustment. The first full-suite run
was interrupted without a result and is not a passing receipt.

Fresh November 17 verification: 33 Flutter voice/settings tests and three typed
client voice API tests passed; desktop analysis reported no issues. The desktop
contract validator passed for 107 runtime settings. These checks use cached tools
and fixtures without live audio. Full-suite results are recorded separately below.

| Check | Final result | Local receipt |
| --- | --- | --- |
| Full Python run | 3,842 passed, 46 skipped, 17 gateway failures; 122 subtests passed | `tts-final-suite.log` |
| All failed gateway cases plus affected voice regressions | 98 passed with cached virtual-environment Python | `tts-final-gateway-voice.log` |
| Full Flutter desktop rerun | 626 passed, 14 skipped | `tts-final-desktop-verified.log` |
| Full typed Dart client suite | 62 passed, 2 skipped | `tts-final-client-suite.log` |
| Desktop analysis | No issues | `tts-final-analyze.log` |

All 17 Python failures came from gateway child processes unable to import `mcp`:
the initial runner added cached dependencies only to its parent interpreter.
Using `.ci-venv313/Scripts/python.exe -B -m pytest` gives child processes the
same installed environment. Every failed case passed in the 98-test rerun,
without production changes. These are combined receipts, not one completely
green full Python invocation. No known test failure remains in these checks.

The first full desktop run found one outdated management test: it supplied an
explicit `--payload` candidate while expecting automatic installed-payload
resolution. The fixture now omits that override for the automatic-resolution
case. The separate explicit-candidate review/apply regression remains intact;
the combined 13 management tests passed. No maintenance production code changed
in this follow-up.

The verified wheel is `odessy-0.5.0.dev0-py3-none-any.whl`, SHA-256
`f29862a57a57a32efeb00a2e79d0b39c2d6b38b3635f2f87354d995774a23d3d`.
Its five changed production modules match the current source byte for byte
(`tts-final-wheel-check.json`). This is wheel-content verification, not a new
installer or installed-upgrade qualification. No hosted CI or GPU hardware
qualification is claimed.


## Installed follow-up — November 17, 2026

The combined update is now verified on `D:\Odessy-test`. The [installed follow-up receipt](../verification/combined-update-2026-11-17.md) records a fresh benchmark using the actual new installed interpreter, short local playback, artifact identity and preserved data. Earlier source measurements and old wheel hashes above remain historical; GPU and human speech acceptance remain pending.
