# Kokoro Inference Optimization Report

Date: 2026-09-11

## Executive Summary

The Jetson Orin Nano Super 4GB now has a successful local PyTorch Kokoro CUDA
benchmark path. For the tested sentence, the warm PyTorch path completed
10/10 measured runs with a median inference time of 515.02 ms. The explicit
ONNX Runtime CPU and CUDA paths also completed successfully, but both remained
near 4.25 seconds per invocation.

The current optimization decision is:

1. Use local PyTorch Kokoro CUDA for the primary low-latency TTS prototype.
2. Retain ONNX CPU and ONNX CUDA as reproducible baselines.
3. Keep TensorRT experimental until it completes model initialization and
   inference without plugin or resource failures.

## Experiment

Hardware and software recorded in the result metadata:

- Jetson Orin Nano Super 4GB
- Linux 5.15.148-tegra-aarch64
- Python 3.10.12
- PyTorch 2.8.0
- ONNX Runtime 1.24.0
- Text: `Odessy is a local edge AI assistant.`
- Warmup: 2 runs
- Measured warm runs: 10
- Measured cold runs: 5

Warm and cold semantics are defined by the benchmark harness. Warm
initialization occurs once and is excluded from steady-state samples. Cold
initialization occurs for every measured invocation and is reported
separately from inference.

## Results

### Warm Comparison

Source: `benchmark/results/20260911-145646`

| Execution path | Provider selected | Success | Init once | P50 | P95 | P99 | Mean RTF |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| PyTorch Kokoro | `PyTorch:cuda` | 10/10 | 12121.91 ms | 515.02 ms | 529.30 ms | 531.81 ms | 0.167 |
| ONNX CPU | `CPUExecutionProvider` | 10/10 | 1596.56 ms | 4247.84 ms | 4307.06 ms | 4311.23 ms | 1.737 |
| ONNX CUDA | `CUDAExecutionProvider`, CPU fallback | 10/10 | 2526.97 ms | 4261.27 ms | 4318.80 ms | 4324.46 ms | 1.743 |

Relative to ONNX CPU, PyTorch warm inference is approximately 87.9 percent
lower in median latency. Relative to ONNX CUDA, it is approximately 87.9
percent lower as well.

### Cold Comparison

Source: `benchmark/results/20260911-145924`

| Execution path | Success | P50 total | P50 inference | P50 initialization |
| --- | ---: | ---: | ---: | ---: |
| PyTorch Kokoro | 5/5 | 4966.34 ms | 548.72 ms | 4423.99 ms |
| ONNX CPU | 5/5 | 5691.62 ms | 4272.43 ms | 1419.27 ms |
| ONNX CUDA | 5/5 | 6770.30 ms | 4317.91 ms | 2459.83 ms |

PyTorch has the lowest cold total median in this run despite the highest
one-time model initialization median. Its cold tail is wide, so this path
should be used warm in the assistant process rather than recreated per
utterance.

## Interpretation

The benchmark measures TTS inference through WAV output completion. The
PyTorch path uses the local full Kokoro model and `af_heart`; the current ONNX
adapters use the existing ONNX model and their configured/default voice path.
The generated audio durations differ:

- PyTorch: approximately 3100 ms
- ONNX: approximately 2453 ms

Therefore, this is a valid execution-path decision for the current system, but
not yet a strict apples-to-apples model-quality or per-sample throughput
experiment. A follow-up controlled comparison should use the same voice,
matching text segmentation, and verify output audio quality.

The PyTorch mean RTF of 0.167 is below 1.0, indicating that it generated the
tested audio faster than real time. The ONNX mean RTF values around 1.74 are
above 1.0, indicating slower-than-real-time generation for this workload.

## Failures And Resolutions

- ONNX CUDA previously failed with `CUBLAS_STATUS_ALLOC_FAILED`. Running with
  the Jetson-native `LD_LIBRARY_PATH` ordering allowed CUDA provider
  initialization to succeed.
- PyTorch Kokoro initially attempted Hugging Face voice resolution. The
  adapter now loads the local model and `af_heart.pt` voice file.
- PyTorch Kokoro initially reported no audio because the adapter treated
  generator results as tuples. It now consumes `KPipeline.Result.audio` and
  concatenates all yielded chunks.
- NvMap allocation messages remain visible during successful PyTorch runs.
  They are unresolved warnings and require separate Jetson memory telemetry.

Detailed incident history is in
[`../failures/failure-006-jetson-cuda-and-pytorch-kokoro.md`](../failures/failure-006-jetson-cuda-and-pytorch-kokoro.md).

## Recommended Next Steps

1. Keep the PyTorch Kokoro engine warm for the lifetime of the voice service.
2. Add time-to-first-audio and chunk playback measurements for interactive
   speech; total WAV completion is not sufficient for perceived latency.
3. Repeat the comparison with the same voice and controlled text segmentation.
4. Correlate the NvMap warnings with `tegrastats` without changing memory
   limits speculatively.
5. Benchmark TensorRT separately only after it can complete a valid Kokoro
   initialization and inference run.
