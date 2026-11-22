from __future__ import annotations

import logging
from pathlib import Path
from typing import Any


SUPPORTED_PROVIDER_MODES = {"auto", "tensorrt", "cuda", "cpu"}

def build_execution_providers(
    provider: str,
    *,  # NOTE: keywork arguments enforced
    cache_dir: Path | None = None,
    available: list[str] | None = None,
) -> list[Any]:
    mode = provider.lower()
    if mode not in SUPPORTED_PROVIDER_MODES:
        raise ValueError(f"Unknown ONNX provider mode: {provider}")

    if available is None:
        import onnxruntime as ort

        available = list(ort.get_available_providers())
    available_set = set(available)

    if mode == "tensorrt" or (
        mode == "auto" and "TensorrtExecutionProvider" in available_set
    ):
        if "TensorrtExecutionProvider" not in available_set:
            raise RuntimeError(
                "TensorRT provider unavailable. "
                f"Available providers: {sorted(available_set)}"
            )
        cache_path = cache_dir or Path(".ort-cache")
        cache_path.mkdir(parents=True, exist_ok=True)
        providers: list[Any] = [
            (
                "TensorrtExecutionProvider",
                {
                    "device_id": 0,
                    "trt_engine_cache_enable": True,    # Save TensorRT engine files to disk
                    "trt_engine_cache_path": str(cache_path),
                    "trt_timing_cache_enable": True,    # Save timing cache (kernel timing info)
                    "trt_timing_cache_path": str(cache_path),
                    "trt_fp16_enable": True,    # Enable FP16 precision for faster inference
                    "trt_max_workspace_size": 536870912,  # 512 MiB
                },
            ),
            (
                "CUDAExecutionProvider",
                {
                    "device_id": 0,
                    "gpu_mem_limit": 512 * 1024 * 1024,  # 512 MiB
                },
            ),
            "CPUExecutionProvider",
        ]
        return [
            item
            for item in providers
            if (item[0] if isinstance(item, tuple) else item) in available_set
        ]

    if mode == "cuda" or (
        mode == "auto" and "CUDAExecutionProvider" in available_set
    ):
        if "CUDAExecutionProvider" not in available_set:
            raise RuntimeError(
                "CUDA provider unavailable. "
                f"Available providers: {sorted(available_set)}"
            )
        providers = [
            (
                "CUDAExecutionProvider",
                {
                    "device_id": 0,
                    "gpu_mem_limit": 512 * 1024 * 1024,  # 512 MiB
                },
            ),
            "CPUExecutionProvider",
        ]
        return [
            item
            for item in providers
            if (item[0] if isinstance(item, tuple) else item) in available_set
        ]

    if mode == "auto":
        accelerators = [name for name in (
            "ROCMExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider"
        ) if name in available_set]
        if accelerators:
            return accelerators + (["CPUExecutionProvider"] if "CPUExecutionProvider" in available_set else [])

    if "CPUExecutionProvider" not in available_set:
        raise RuntimeError(
            "CPU provider unavailable. "
            f"Available providers: {sorted(available_set)}"
        )
    return ["CPUExecutionProvider"]


def build_kokoro(
    model_path: str,
    voices_path: str,
    *,
    provider: str = "auto",
    cache_dir: Path | None = None,
) -> Any:
    import onnxruntime as ort
    from kokoro_onnx import Kokoro

    providers = build_execution_providers(
        provider,
        cache_dir=cache_dir,
        available=list(ort.get_available_providers()),
    )
    options = {}
    if "DmlExecutionProvider" in providers:
        # DirectML requires sequential execution and disabled memory patterns.
        session_options = ort.SessionOptions()
        session_options.enable_mem_pattern = False
        session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options["sess_options"] = session_options
    try:
        session = ort.InferenceSession(model_path, providers=providers, **options)
    except Exception:
        # A registered GPU provider can still have a missing driver/library or an
        # unsupported model. Only automatic selection may fall back silently.
        if (provider.lower() != "auto" or providers == ["CPUExecutionProvider"]
                or "CPUExecutionProvider" not in ort.get_available_providers()):
            raise
        logging.getLogger(__name__).warning("Automatic ONNX accelerator initialization failed; using CPU")
        session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    return Kokoro.from_session(session, voices_path)
