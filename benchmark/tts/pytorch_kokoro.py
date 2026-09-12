"""Pure PyTorch Kokoro adapter for the benchmark matrix."""

from __future__ import annotations

import inspect
import os
import time
from pathlib import Path
from typing import Any

class PyTorchKokoroEngine:
    """Run Kokoro through its PyTorch pipeline without ONNX Runtime."""

    sample_rate = 24000

    def __init__(
        self,
        *,
        lang_code: str = "a",
        voice: str = "af_heart",
        speed: float = 1.0,
        device: str | None = None,
        model_dir: str | Path | None = None,
    ) -> None:
        self.lang_code = lang_code
        self.voice = voice
        self.speed = speed
        self.requested_device = device
        self.model_dir = Path(model_dir) if model_dir else None
        self.device: str | None = None
        self._pipeline: Any | None = None
        self._voice_tensor: Any | None = None
        self._runtime_info: Any | None = None

    def initialize(self) -> Any:
        from ..adapters import RuntimeConfigurationError

        try:
            import torch
        except ImportError as exc:
            info = _runtime_info(
                runtime_version=None,
                device=None,
                lang_code=self.lang_code,
            )
            raise RuntimeConfigurationError(
                f"PyTorch Kokoro unavailable: {exc}",
                runtime_info=info,
            ) from exc

        if self.model_dir is None:
            configured_model_dir = os.environ.get(
                "ODESSY_KOKORO_PYTORCH_MODEL_DIR"
            )

            if configured_model_dir:
                self.model_dir = Path(
                    os.path.expandvars(
                        os.path.expanduser(configured_model_dir)
                    )
                )

        selected_device = self.requested_device
        if selected_device is None:
            selected_device = "cuda" if torch.cuda.is_available() else "cpu"

        try:
            from kokoro import KModel, KPipeline
        except ImportError as exc:
            info = _runtime_info(
                runtime_version=getattr(torch, "__version__", None),
                device=selected_device,
                lang_code=self.lang_code,
            )
            raise RuntimeConfigurationError(
                f"PyTorch Kokoro package unavailable: {exc}",
                runtime_info=info,
            ) from exc

        try:
            # Use a local Kokoro-82M directory when provided.
            #
            # Expected files:
            #   config.json
            #   kokoro-v1_0.pth
            #   voices/af_heart.pt
            if self.model_dir is not None:
                config_path = self.model_dir / "config.json"
                model_path = self.model_dir / "kokoro-v1_0.pth"

                missing = [
                    str(path)
                    for path in (config_path, model_path)
                    if not path.is_file()
                ]
                if missing:
                    raise FileNotFoundError(
                        "Missing local Kokoro model files: "
                        + ", ".join(missing)
                    )

                model = KModel(
                    repo_id="hexgrad/Kokoro-82M",
                    config=str(config_path),
                    model=str(model_path),
                ).to(selected_device).eval()

                pipeline_kwargs = {
                    "lang_code": self.lang_code,
                    "model": model,
                    "repo_id": "hexgrad/Kokoro-82M",
                }

                # Some Kokoro versions accept device; others infer it
                # from the supplied KModel.
                if "device" in inspect.signature(KPipeline).parameters:
                    pipeline_kwargs["device"] = selected_device

                pipeline = KPipeline(**pipeline_kwargs)

                voice_path = self.model_dir / "voices" / f"{self.voice}.pt"
                if not voice_path.is_file():
                    raise FileNotFoundError(
                        f"Missing local Kokoro voice file: {voice_path}"
                    )
                self._voice_tensor = torch.load(
                    str(voice_path),
                    map_location=selected_device,
                    weights_only=True,
                )
                voice_cache = getattr(pipeline, "voices", None)
                if not isinstance(voice_cache, dict):
                    raise RuntimeError(
                        "Kokoro pipeline does not expose a voice cache; "
                        "cannot guarantee offline voice loading"
                    )
                voice_cache[self.voice] = self._voice_tensor
            else:
                pipeline_kwargs = {"lang_code": self.lang_code}

                if "device" in inspect.signature(KPipeline).parameters:
                    pipeline_kwargs["device"] = selected_device
                elif self.requested_device is not None:
                    raise RuntimeConfigurationError(
                        "Installed Kokoro KPipeline cannot honor an explicit device; "
                        "upgrade the kokoro package.",
                        runtime_info=_runtime_info(
                            runtime_version=getattr(torch, "__version__", None),
                            device=selected_device,
                            lang_code=self.lang_code,
                        ),
                    )

                pipeline = KPipeline(**pipeline_kwargs)

        except Exception as exc:
            raise RuntimeConfigurationError(
                f"PyTorch Kokoro initialization failed: {exc}",
                runtime_info=_runtime_info(
                    runtime_version=getattr(torch, "__version__", None),
                    device=selected_device,
                    lang_code=self.lang_code,
                ),
            ) from exc

        self.device = selected_device
        self._pipeline = pipeline
        self._runtime_info = _runtime_info(
            runtime_version=getattr(torch, "__version__", None),
            device=selected_device,
            lang_code=self.lang_code,
        )
        return self._runtime_info

    def synthesize(self, text: str, output_path: Path) -> Any:
        from ..adapters import TTSMeasurement

        import soundfile as sf

        if self._pipeline is None:
            raise RuntimeError("PyTorch Kokoro engine is not initialized")

        started = time.perf_counter()
        generated = self._pipeline(
            text,
            voice=self.voice,
            speed=self.speed,
        )
        chunks = []
        for result in generated:
            audio = getattr(result, "audio", None)
            if audio is None and isinstance(result, tuple) and len(result) >= 3:
                audio = result[2]
            if audio is not None:
                chunks.append(_to_numpy(audio))
        if not chunks:
            raise RuntimeError("PyTorch Kokoro produced no audio")

        samples = chunks[0] if len(chunks) == 1 else _concatenate(chunks)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(output_path, samples, self.sample_rate)
        return TTSMeasurement(
            inference_ms=(time.perf_counter() - started) * 1000.0,
            audio_duration_ms=_audio_duration_ms(samples, self.sample_rate),
        )

    def close(self) -> None:
        self._pipeline = None
        self._voice_tensor = None
        self._runtime_info = None
        self.device = None


def _runtime_info(
    *,
    runtime_version: str | None,
    device: str | None,
    lang_code: str,
) -> Any:
    from ..adapters import RuntimeInfo

    selected = [f"PyTorch:{device}"] if device else None
    return RuntimeInfo(
        requested_provider_mode="pytorch",
        available_execution_providers=None,
        requested_execution_providers=["PyTorch"],
        session_execution_providers=selected,
        runtime_version=runtime_version,
        provider_options={
            "device": device,
            "lang_code": lang_code,
        },
        provider_selection_status="selected" if device else "unavailable",
    )


def _to_numpy(audio: Any) -> Any:
    detach = getattr(audio, "detach", None)
    if callable(detach):
        audio = detach()
    cpu = getattr(audio, "cpu", None)
    if callable(cpu):
        audio = cpu()
    numpy = getattr(audio, "numpy", None)
    if callable(numpy):
        return numpy()
    return audio


def _concatenate(chunks: list[Any]) -> Any:
    try:
        import numpy as np
    except ImportError:
        return [sample for chunk in chunks for sample in chunk]
    return np.concatenate(chunks)


def _audio_duration_ms(samples: Any, sample_rate: int) -> float | None:
    try:
        return len(samples) / float(sample_rate) * 1000.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None
