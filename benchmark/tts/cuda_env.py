"""Jetson-native CUDA environment helpers for benchmark subprocesses."""

from __future__ import annotations

import os

JETSON_NATIVE_CUDA_LIBRARY_ENTRIES = (
    "/opt/ros/humble/lib/aarch64-linux-gnu",
    "/opt/ros/humble/lib",
    "/usr/local/cuda-12.6/lib64",
    "/usr/local/cuda-12.6/targets/aarch64-linux/lib",
)


def build_jetson_native_cuda_library_path(
    existing: str | None = None,
) -> str:
    """Prepend the known-good Jetson CUDA directories to an existing path."""
    current = os.environ.get("LD_LIBRARY_PATH") if existing is None else existing
    entries = list(JETSON_NATIVE_CUDA_LIBRARY_ENTRIES)
    if current:
        entries.extend(
            entry
            for entry in current.split(os.pathsep)
            if entry and entry not in entries
        )
    return os.pathsep.join(entries)


def jetson_native_cuda_path_is_complete(existing: str | None = None) -> bool:
    current = os.environ.get("LD_LIBRARY_PATH") if existing is None else existing
    entries = set((current or "").split(os.pathsep))
    return all(required in entries for required in JETSON_NATIVE_CUDA_LIBRARY_ENTRIES)
