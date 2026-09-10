from __future__ import annotations

from pathlib import Path
from typing import Any

def build_kokoro(
    model_path: str,
    voices_path: str,
    *,
    provider: str = "auto",
    cache_dir: Path | None = None,
) -> Any:
    import onnxruntime as ort
    from kokoro_onnx import Kokoro

    available = set(ort.get_available_providers())
    mode = provider.lower()

    if mode not in {"auto", "tensorrt", "cuda", "cpu"}:
        raise ValueError(f"Unknown ONNX provider mode: {provider}")

    use_tensorrt = (
        mode == "tensorrt"
        or (mode == "auto" and "TensorrtExecutionProvider" in available)
    )

    if use_tensorrt:
        if "TensorrtExecutionProvider" not in available:
            raise RuntimeError(
                "TensorRT provider is unavailable. "
                f"Available providers: {sorted(available)}"
            )

        cache_path = cache_dir or Path(".ort-cache")
        cache_path.mkdir(parents=True, exist_ok=True)
        providers: list[Any] = [
            (
                "TensorrtExecutionProvider",
                {
                    "device_id": 0,
                    "trt_engine_cache_enable": True,
                    "trt_engine_cache_path": str(cache_path),
                    "trt_timing_cache_enable": True,
                    "trt_timing_cache_path": str(cache_path),
                },
            )
        ]

        if "CUDAExecutionProvider" in available:
            providers.append("CUDAExecutionProvider")
        if "CPUExecutionProvider" in available:
            providers.append("CPUExecutionProvider")

    elif mode == "cuda" or (
        mode == "auto" and "CUDAExecutionProvider" in available
    ):
        if "CUDAExecutionProvider" not in available:
            raise RuntimeError(
                "CUDA provider is unavailable. "
                f"Available providers: {sorted(available)}"
            )
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

    else:
        providers = ["CPUExecutionProvider"]

    session = ort.InferenceSession(model_path, providers=providers)
    return Kokoro.from_session(session, voices_path)
