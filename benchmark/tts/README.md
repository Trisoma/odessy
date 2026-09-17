# Odessy Benchmark

This directory contains the **Kokoro execution-path comparison** manifest in
[`profiles.toml`](profiles.toml). The common benchmark harness remains in the
parent [`benchmark/`](..). The first matrix
compares the historical Kokoro integration with explicit ONNX Runtime
execution-provider configurations.

## Profiles

The manifest defines fourteen profiles:

| Execution path | Cold | Warm |
| --- | --- | --- |
| `legacy_constructor` | `legacy_constructor_cold` | `legacy_constructor_warm` |
| `onnx_cpu` | `onnx_cpu_cold` | `onnx_cpu_warm` |
| `onnx_cuda` | `onnx_cuda_cold` | `onnx_cuda_warm` |
| `onnx_tensorrt` | `onnx_tensorrt_cold` | `onnx_tensorrt_warm` |
| `kokoro_pytorch` auto device | `kokoro_pytorch_cold` | `kokoro_pytorch_warm` |
| `kokoro_pytorch` CPU | `kokoro_pytorch_cpu_cold` | `kokoro_pytorch_cpu_warm` |
| `kokoro_pytorch` CUDA | `kokoro_pytorch_cuda_cold` | `kokoro_pytorch_cuda_warm` |

`legacy_constructor` is a historical integration path: it constructs
`kokoro_onnx.Kokoro(...)` directly and leaves provider selection implicit. It
is not an equivalent execution provider.

`onnx_cpu`, `onnx_cuda`, and `onnx_tensorrt` are explicit ONNX Runtime paths.
They make the provider request visible in the benchmark metadata so results
can distinguish the requested configuration from the providers actually
available and used. The CUDA path requests CUDA first, with CPU as fallback.

`kokoro_pytorch` uses Kokoro's `KPipeline` directly through PyTorch. It is a
separate implementation/runtime path and does not use the existing ONNX model
or ONNX Runtime session. The `pytorch_device` profile field selects `auto`,
`cpu`, or `cuda`. Its model loading time is included in cold initialization
and excluded from warm steady-state samples.

## Provider Priority

The TensorRT path must use this exact ONNX Runtime priority order:

```python
[
    ("TensorrtExecutionProvider", {...}),
    ("CUDAExecutionProvider", {...}),
    "CPUExecutionProvider",
]
```

ONNX Runtime gives earlier providers higher priority when assigning graph
nodes. The TensorRT profile is strict: if
`TensorrtExecutionProvider` is unavailable, the run must record a failed
result instead of silently measuring CUDA or CPU.

Each run should record:

- requested provider mode and provider options;
- `onnxruntime.__version__`;
- `onnxruntime.get_available_providers()`;
- the session's `get_providers()` result;
- provider selection status and any error.

For the legacy path, an underlying session may be hidden by the wrapper. In
that case, record the session providers as unavailable/`null` and mark the
selection status as `not_observable`; do not infer them.

## Cold And Warm Semantics

**Cold** constructs and initializes a new TTS engine for every measured
invocation. Record initialization and inference separately:

```text
total_ms = initialization_ms + inference_ms
```

**Warm** initializes one engine before warmups, reuses it for all measured
invocations, and excludes one-time initialization from steady-state samples.
Warm samples therefore contain `initialization_ms = null` and
`total_ms = inference_ms`; the one-time initialization value belongs in run
metadata.

TTS inference runs through WAV output completion. Record generated audio
duration and calculate:

```text
rtf = inference_ms / audio_duration_ms
```

Summaries should report initialization separately from steady-state
`inference_ms` and `total_ms`, including P50, P95, and P99 where enough
successful samples exist. Failed configurations remain in the output with
`success = false` and an error message.

## Usage

The profile manifest is declarative configuration for the profile-aware
matrix runner. The current scenario runner can still execute the existing TTS
benchmark directly:

```powershell
py -3.10 -m benchmark `
  --scenario tts `
  --adapter real `
  --runs 10 `
  --warmup 2 `
  --text "Odessy is a local AI assistant."
```

Use the manifest profile names with the profile-aware runner:

```powershell
py -3.10 -m benchmark `
  --manifest benchmark/tts/profiles.toml `
  --profile legacy_constructor_cold `
  --profile onnx_cpu_cold `
  --profile onnx_cuda_cold `
  --adapter real `
  --runs 10 `
  --warmup 2 `
  --text "Odessy is a local AI assistant."
```

The matrix command requires `--adapter real` because it measures the actual
Kokoro/runtime integration. Missing optional runtime dependencies or an
unavailable strict provider are recorded as failed samples instead of being
silently replaced by another provider.

Generated TTS results belong under `benchmark/tts/results/` and are ignored by
Git.
The historical Jetson measurements remain in
[`jetson-voice-benchmark.mdx`](jetson-voice-benchmark.mdx), while raw baseline
logs for other benchmark areas remain under [`../base/`](../base/).
