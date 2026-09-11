# Failure 006: Jetson CUDA and PyTorch Kokoro Runtime Issues

Date: 2026-09-11

Environment:

- Jetson Orin Nano Super 4GB
- Python 3.10.12
- PyTorch 2.8.0
- ONNX Runtime GPU 1.24.0
- Kokoro 0.9.4
- Linux 5.15.148-tegra-aarch64

## ONNX Runtime CUDA Initialization Failure

The first ONNX Runtime CUDA benchmark failed during session initialization with:

```text
CUBLAS_STATUS_ALLOC_FAILED
expr=cublasCreate(&cublas_handle&)
```

The same run also emitted an ONNX Runtime DRM discovery warning for:

```text
/sys/class/drm/card1/device/vendor
```

The DRM warning was not the direct failure. The practical resolution was to
launch the benchmark with Jetson-native ROS and CUDA libraries first:

```bash
env LD_LIBRARY_PATH=/opt/ros/humble/lib/aarch64-linux-gnu:/opt/ros/humble/lib:/usr/local/cuda-12.6/lib64:/usr/local/cuda-12.6/targets/aarch64-linux/lib \
python -m benchmark ...
```

After that library-loading correction, ONNX Runtime selected the requested
providers successfully:

```text
CPU:  CPUExecutionProvider
CUDA: CUDAExecutionProvider, CPUExecutionProvider
```

The DRM discovery and CUDA memcpy messages can still appear. They are warnings
and should remain visible during diagnosis. They must not be treated as proof
that TensorRT or CUDA is failing.

## PyTorch Kokoro Offline Voice Failure

The initial PyTorch path attempted to resolve the voice through the default
Kokoro/Hugging Face mechanism, producing requests such as:

```text
HEAD https://huggingface.co/hexgrad/Kokoro-82M/resolve/main/voices/af_heart.pt
```

The resolution was to preserve the local model directory and load the voice
file explicitly:

```text
/home/jaimef/models/Kokoro-82M/config.json
/home/jaimef/models/Kokoro-82M/kokoro-v1_0.pth
/home/jaimef/models/Kokoro-82M/voices/af_heart.pt
```

The configured environment variable is:

```text
ODESSY_KOKORO_PYTORCH_MODEL_DIR=$HOME/models/Kokoro-82M
```

The adapter loads the local voice tensor and supplies it through Kokoro's
voice cache. The explicit `repo_id="hexgrad/Kokoro-82M"` arguments only remove
the defaulting warning; they do not replace the local model files.

## PyTorch Generator Extraction Failure

The first benchmark implementation assumed that each pipeline yield was a
tuple. Kokoro 0.9.4 actually yields `KPipeline.Result` objects with an
`audio` field. This caused successful CUDA inference to be reported as:

```text
PyTorch Kokoro produced no audio
```

The adapter was corrected to consume the generator, extract
`result.audio`, convert the Torch tensor to the WAV representation, and
concatenate multiple results in temporal order.

## Remaining NvMap Warning

Successful PyTorch runs still emitted:

```text
NvMapMemAllocInternalTagged: ... error 12
NvMapMemHandleAlloc: error 0
```

These messages did not produce benchmark failures in the recorded runs, but
they indicate a Jetson memory-allocation condition that is not resolved by the
benchmark changes. They should be correlated with `tegrastats` in a separate
investigation. No memory-limit or CUDA-provider changes were made in response
to this warning.

## Evidence

Successful result sets:

- `benchmark/results/20260911-145646`: warm comparison, 10 measured runs
- `benchmark/results/20260911-145924`: cold comparison, 5 measured runs

Both result sets completed with zero measured failures for PyTorch, ONNX CPU,
and ONNX CUDA.
