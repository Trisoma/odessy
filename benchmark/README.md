# Odessy Benchmark

This package contains the shared benchmark harness for Odessy.

For agent-behaviour evaluations (safety, command generation, tool selection, and
multi-step tasks), use [`evals/`](../evals/README.md). That suite supports local
and cloud models, including DeepSeek low-effort configuration, and records
provenance diagnostics. This component-performance harness has a separate
configuration and artifact format; the eval metadata additions do not change it.

## Shared Components

- `cli.py`: command-line entry point and scenario selection.
- `core.py`: runner, samples, outcomes, and summary calculations.
- `scenarios.py`: reusable STT, model, TTS, voice-turn, and matrix scenarios.
- `adapters.py`: runtime-facing benchmark adapters and common interfaces.
- `profiles.py`: Python 3.10-compatible TOML profile loading.
- `export.py` and `telemetry.py`: result export and resource measurements.
- `plot_results.R`: shared result visualization script.

TTS-specific profiles, adapters, historical reports, commands, and generated
results are organized under [`tts/`](tts/). Baseline logs for other benchmark
areas remain under [`base/`](base/).

## Usage

Run a general scenario from the repository root:

```powershell
py -3.10 -m benchmark --scenario tts --adapter real
```

For the Kokoro execution-path matrix, see
[`tts/README.md`](tts/README.md) and use its manifest:

```powershell
py -3.10 -m benchmark `
  --manifest benchmark/tts/profiles.toml `
  --profile kokoro_pytorch_warm `
  --profile onnx_cpu_warm `
  --profile onnx_cuda_warm `
  --adapter real
```

TTS scenario and matrix results are written by default under
`benchmark/tts/results/`. Explicit `--output-dir` values still take precedence.

## LFM Latency Comparison

Run the proposal-only comparison without constructing STT or TTS:

```bash
python -m benchmark \
  --scenario lfm_compare \
  --adapter real \
  --prompt "What time is it?" \
  --expected-tool time.now \
  --lfm-variant current=lfm2.5-thinking:1.2b-q8_0 \
  --lfm-variant minimal=lfm2.5-thinking:1.2b-q8_0@false \
  --lfm-variant low=lfm2.5-thinking:1.2b-q8_0@low \
  --runs 3 \
  --warmup 1
```

Each row reports Ollama model latency, prompt evaluation time, generation
time, generated tokens, generation throughput, call count, proposal validity,
and expected-tool correctness. Missing reasoning-token counts remain empty
unless Ollama exposes them.

## Warm vs cold semantics

Every run records `is_warmup` per sample, and configures
`warmup_iterations` / `measurement_iterations` where the scenario supports them.
This section defines what those mean, so two runs can be compared honestly.

```text
cold   a fresh process with no prior initialization in it. Every model, session,
       and provider load is paid inside the first measured sample.

warm   the same process as the preceding samples, with those artefacts already
       resident. Warmup iterations are discarded and never reported.
```

Rules any comparison must follow:

```text
1. Never compare a cold median against a warm median. They measure different work.
2. Discard every is_warmup sample. Only is_warmup = false enters a median.
3. Report the median and p95. Do not report a mean: the distributions are
   long-tailed, and one stalled sample moves a mean arbitrarily.
4. Hold the inputs fixed across variants: same audio file or prompt, same voice,
   same provider, same iteration counts.
5. State the provider and device in the summary. The same scenario under ONNX CPU
   and PyTorch CUDA is two different benchmarks, not two points on one curve.
6. Record the model-call progress timeout when comparing model latency, since it
   changes which runs can complete at all.
```

### Historical ONNX/TTS measurement caveat

Before session reuse, warm/cold distinguished **OS page-cache warmth, not model
warmth** on the ONNX path. `KokoroTTS._synthesize_with_kokoro` previously called
`build_kokoro` -- constructing a fresh `ort.InferenceSession` -- on every
`synthesize()`, so a "warm" iteration still reloaded the weights and gained only
page-cache speed.

Session reuse was added on 2026-09-24 (`build_kokoro_cached` in `voice/tts.py`),
so warm and cold now differ by session residency as well. **Recorded TTS results
from before that change are not comparable to results after it**, and this is the
most likely cause of the RSS growth observed in
`benchmark/tts/results/20260917-035143` (56.8 MB -> ~1.44 GB -> ~1.89 GB).

Re-measure the TTS baseline before drawing any conclusion about warm-vs-cold
performance.
