# CPU speech synthesis comparison

## Selected direction: Piper replaces Kokoro

**User decision, November 17, 2026:** use Piper as Odessy's replacement TTS
engine. Stop further Kokoro performance tuning; retain its old benchmark evidence
and compatibility consumers until the production migration is verified.

**Piper is Odessy's selected TTS engine.** The migration acceptance checklist
below tracks implementation and packaging separately from that decision. The
[license review](license-review.md) records the remaining GPL/native/model
distribution decisions; no installer qualification or relicensing is implied.

Migration acceptance: implement the existing TTS interface with warm model reuse,
bounded CPU threads and explicit errors; preserve Stop, active audio protection,
STT/wake, settings persistence and provider ownership. Validate source and then a
matching installer/update with pinned resources/notices before removing Kokoro
from the production dependency closure. Default to a conservative four-thread
evaluation candidate; 8 is slightly faster here, while 16 degraded badly. This
is a proposed integration default, not a changed application setting.

November 17, 2026. Benchmark-only experiment; Odessy's selected TTS provider,
production dependencies, installer and installed application are unchanged.

Piper is an alternative local ONNX TTS engine, not a required replacement for
Kokoro. This experiment uses Piper 1.3.0 with `en_US-ljspeech-medium` and the
existing Kokoro int8 model with `af_sarah`. Voices differ; this is not a
quality-matched contest. Human listening and production voice integration remain
pending. No microphone, speaker playback, paid provider or GPU is used.

## Results on this laptop

Intel Core i7-13620H, 10 cores / 16 logical processors, Windows, Python 3.11.9.
Piper uses ONNX Runtime 1.32.0; the existing Kokoro environment uses 1.31.1 and
kokoro-onnx 0.6.1. Model and benchmark hashes are in the JSON receipts.

Piper confirmation run: three repetitions per text per session, medians below.
Model/session load (0.82–1.22 s) and the first short request (0.12–0.25 s) are
recorded separately. Audio is generated into memory and discarded. The answer
produces approximately 12.4 seconds of speech; timings exclude file I/O/playback.

| Intra-op threads | Short synthesis | Answer synthesis | First answer chunk | Answer RTF |
| --- | ---: | ---: | ---: | ---: |
| 1 | 0.687 s | 4.317 s | 0.436 s | 0.346 |
| 2 | 0.346 s | 0.688 s | 0.070 s | 0.055 |
| 4 | 0.073 s | 0.472 s | 0.052 s | 0.037 |
| 8 | 0.062 s | 0.384 s | 0.046 s | 0.031 |
| 16 | 1.598 s | 9.038 s | 1.333 s | 0.728 |
| ONNX default (0) | 0.063 s | 0.406 s | 0.048 s | 0.033 |

RTF is synthesis time / generated audio duration; below 1 means faster than
playback. First audio means a complete sentence chunk returned by the API,
not word-by-word waveform streaming or audible device latency. Piper is fast
enough for realtime playback here. Eight threads was fastest in this small sweep;
four consumed less CPU time and may leave more room for STT/agent work. Sixteen
was substantially worse. No process priority, affinity or system settings changed.

The combined sweep (two repeats) also found fast Piper performance. All Kokoro
profiles (1/2/4/8/default) exceeded the **50-second per-profile** budget. This is
not a 50-second per-utterance claim. Stack dumps place the delay inside ONNX
inference, not playback. The cached model and voice hashes match the selected
installed assets. Current Kokoro performance remains unresolved; no fair Kokoro
speedup ratio or best thread count can be reported. The older 22–28 second
measurements are separate historical evidence, not this comparison's baseline.

Receipts: [combined sweep](receipts/2026-11-17/comparison.json) (intentional nonzero
exit for failed Kokoro profiles) and [Piper confirmation](receipts/2026-11-17/piper-confirmation.json).
The initial low-quality Lessac exploratory run is excluded from recommendations:
its model card links restrictive dataset terms. The LJ Speech card declares
public-domain source data. The confirmation's first profiles overlapped the tail
of the combined run; background load, thermals, fixed profile order, stochastic
Piper output and small sample counts limit precise comparisons. Repeat on an idle
machine before selecting product defaults. No perceptual-quality rating is claimed.

## Local files and installation

- Reusable code: `benchmark/tts/cpu_compare.py` (standard-library controller;
  optional engine packages are imported only in subprocess workers).
- Piper runtime: `.qualification/tts-cpu-comparison-20261117/piper-env/`.
- Piper assets: `.voice-cache/piper/en_US-ljspeech-medium/`, beside Kokoro's
  `.voice-cache/kokoro-v1.0.int8.onnx` and `.voice-cache/voices-v1.0.bin`.
- Exploratory voice: `.voice-cache/piper/en_US-lessac-low/` (do not package).
- Logs: `.qualification/tts-cpu-comparison-20261117/`.

These ignored runtime/cache directories are disposable benchmark artifacts;
they are not automatically included in releases. No global installs were used.
Piper **GPL-3.0-or-later** requires a deliberate distribution/license decision
before combining it with Odessy's Apache-2.0 product. This experiment does not
approve bundling, model redistribution or change the application's license.

For a fresh **benchmark** environment (explicit installation, not performed by
the script), from the repository root with Python 3.11 and uv:

```powershell
uv venv --python 3.11 .qualification/tts-cpu/piper-env
uv pip install --python .qualification/tts-cpu/piper-env/Scripts/python.exe --require-hashes -r benchmark/tts/requirements/piper.lock
```

Get the model, JSON config and `MODEL_CARD` from the
[voice directory](https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/ljspeech/medium).
Place them in `.voice-cache/piper/en_US-ljspeech-medium/`; verify their SHA-256
values against the receipt's `assets.piper` entries. Do not rename the adjacent
`.onnx.json` file. The benchmark never downloads missing dependencies/assets.

```powershell
python benchmark/tts/cpu_compare.py --piper-python .qualification/tts-cpu/piper-env/Scripts/python.exe --piper-model .voice-cache/piper/en_US-ljspeech-medium/en_US-ljspeech-medium.onnx --threads 1,2,4,8,16,0 --repeats 3 --timeout 120 --output benchmark/results/my-piper.json
```

To include Kokoro, add `--kokoro-python PATH_TO_EXISTING_VOICE_PYTHON`
`--kokoro-model .voice-cache/kokoro-v1.0.int8.onnx`
`--kokoro-voices .voice-cache/voices-v1.0.bin`. No installed path is hardcoded.
Use the same interpreter/runtime versions for stricter engine comparisons.
Profiles run sequentially with fresh sessions; subprocess timeouts kill the
owned Windows worker tree, retain partial measurements where available, and
return nonzero. `--threads 0` means ONNX's default intra-op thread selection,
not zero workers; all sessions use sequential execution and inter-op threads 1.

## Next step

### Verification after organization

The organized runner completed a fresh, sequential three-repeat sweep. No other
benchmark worker was running. [Raw receipt](receipts/2026-11-17/organized-rerun.json):

| Threads | Short synthesis | Answer synthesis | First answer chunk |
| --- | ---: | ---: | ---: |
| 2 | 0.103 s | 0.656 s | 0.073 s |
| 4 | 0.070 s | 0.444 s | 0.051 s |
| 8 | 0.064 s | 0.383 s | 0.043 s |
| ONNX default | 0.062 s | 0.381 s | 0.042 s |

Answer duration is approximately 12.4 seconds. The small difference between eight
and default threads is not evidence of a universal winner. Four remains a useful
candidate when sharing the CPU with STT/agent work. All **32** existing benchmark
harness/matrix/telemetry regression tests passed after the file moves. The
listening CLI and relative asset lookup were checked from their new location;
`git diff --check` passed. The Desktop launcher was updated to the moved script.

### Integration remaining

Piper is selected to replace Kokoro; finish human listening and a source adapter
behind Odessy's existing TTS interface. Preserve capture/wake and
Stop/lifecycle guarantees. A source adapter, packaged assets/dependencies/notices,
and a fresh installer qualification are separate work; this benchmark does not
make Piper selectable in the application. No third engine was needed in this
bounded experiment. No production changes, commit, push or merge were made.
