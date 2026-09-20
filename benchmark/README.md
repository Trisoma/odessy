# Odessy Benchmark

This package contains the shared benchmark harness for Odessy.

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
