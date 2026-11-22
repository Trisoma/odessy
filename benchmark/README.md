# Performance benchmarks

Benchmarks measure specific workloads, not release readiness. Run from the
repository root with an environment containing the selected engine's dependencies.
No benchmark should change application settings, invoke agent tools or download
dependencies implicitly. Real hardware runs are opt-in; fixture checks remain
available without voice models or paid services.

## Start here

| Purpose | Entry point | Guide / evidence |
| --- | --- | --- |
| Piper CPU thread sweep; optional historical Kokoro comparison | `python benchmark/tts/cpu_compare.py --help` | [TTS measurements](tts/README.md) |
| Hear Piper and time your own text | `python benchmark/tts/listen.py --help` | [Listening and license review](tts/license-review.md) |
| Existing voice-loop / execution-path benchmark | `python -m benchmark --help` | Existing `cli.py`, `scenarios.py`, `tts/profiles.toml` |
| Offline regression tests | `python -m pytest tests/test_benchmark_harness.py tests/test_benchmark_matrix.py tests/test_benchmark_telemetry.py -q` | No live speech required |

## Layout

```text
benchmark/
  README.md                 navigation and conventions
  core.py, telemetry.py     shared measurement/telemetry APIs
  cli.py, scenarios.py      existing voice-loop runner and scenarios
  adapters.py, profiles.py  existing engine adapters and profile parsing
  export.py, fakes.py        reports and deterministic fixture support
  tts/
    README.md               CPU method, results, migration decision
    cpu_compare.py          isolated engine/thread sweep
    listen.py               standalone WAV/listening preview
    license-review.md       distribution assessment and preview commands
    requirements/           pinned benchmark-only Piper dependencies
    receipts/2026-11-17/    curated machine-readable measurements
    profiles.toml           retained historical Kokoro execution profiles
    pytorch_kokoro.py        retained historical comparison adapter
    cuda_env.py             existing optional CUDA launch support
  results/                   ignored local scratch reports/audio
```

The established Python modules retain their import paths for existing consumers;
the new speech experiment no longer scatters scripts/locks/results at this root.
Curated receipts are dated evidence; never rewrite an old run to describe newer
source. Historical Kokoro scenarios remain reproducible while Piper integration
is pending. Generated WAVs, environment directories and model weights are not
source artifacts and must not be added to receipts.
The existing execution-path runner also writes under ignored `tts/results/`;
those are scratch outputs, distinct from curated `tts/receipts/` evidence.

## Reproducibility and cleanup

Record model hashes, package versions, thread settings, hardware, repetitions,
timeouts and measurement scope. Compare generated audio duration as well as wall
time. First returned audio is not speaker latency; faster synthesis does not
prove acceptable voice quality. Report failed profiles rather than dropping them.

Use `tts/requirements/piper.lock` only in an isolated benchmark environment.
Cache Piper voices under `.voice-cache/piper/`, alongside the existing Kokoro
assets. Current runtime/logs are in `.qualification/tts-cpu-comparison-20261117/`.
Those ignored paths and local `results/` are disposable after checking no preview
is using them. Keep scripts, locks, notices and curated JSON receipts. This layout
does not add Piper to the installer or change any production dependency.
