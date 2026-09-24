from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .onnx_runtime import build_kokoro

Synthesizer = Callable[[str, Path], None]

#: Process-wide cache for built Kokoro instances.
#:
#: ``build_kokoro`` constructs a fresh ``ort.InferenceSession`` holding the model
#: weights, and it used to be called on every ``synthesize()``. That is invisible
#: in production only because the voice loop runs one bounded turn per process;
#: in the benchmark, which runs many turns in one process, it showed as RSS
#: climbing from 56.8 MB to about 1.44 GB and then 1.89 GB.
#:
#: The cache is module-level rather than an attribute of ``KokoroTTS`` because the
#: dataclass is frozen and may be reconstructed between turns, while the expensive
#: artefact belongs to the process. Keyed by everything that changes the result.
#:
#: ``cache_dir`` here is the TensorRT *engine* cache directory, not a session
#: cache -- the name made the missing session reuse easy to miss.
_KOKORO_CACHE: dict[tuple[str, str, str, str | None], Any] = {}
_KOKORO_CACHE_LOCK = threading.Lock()


def _kokoro_cache_key(
    model_path: str, voices_path: str, provider: str, cache_dir: Path | None
) -> tuple[str, str, str, str | None]:
    return (model_path, voices_path, provider, None if cache_dir is None else str(cache_dir))


def build_kokoro_cached(
    model_path: str,
    voices_path: str,
    *,
    provider: str = "auto",
    cache_dir: Path | None = None,
) -> Any:
    """Return a Kokoro instance, building it at most once per distinct key.

    The build happens outside the lock: loading the model is slow, and holding a
    lock across it would serialise unrelated callers. A concurrent double build is
    possible and harmless -- ``setdefault`` keeps the first result and the other
    is simply discarded.
    """

    key = _kokoro_cache_key(model_path, voices_path, provider, cache_dir)

    with _KOKORO_CACHE_LOCK:
        cached = _KOKORO_CACHE.get(key)
    if cached is not None:
        return cached

    kokoro = build_kokoro(
        model_path, voices_path, provider=provider, cache_dir=cache_dir
    )

    with _KOKORO_CACHE_LOCK:
        return _KOKORO_CACHE.setdefault(key, kokoro)


def reset_kokoro_cache() -> None:
    """Drop cached sessions. For tests and for explicit lifecycle control."""

    with _KOKORO_CACHE_LOCK:
        _KOKORO_CACHE.clear()

@dataclass(frozen=True)
class TTSResult:
    output_path: Path
    status: str
    error: str | None = None

@dataclass(frozen=True)
class KokoroTTS:
    model_path: str
    voices_path: str
    voice: str = "af_sarah"
    speed: float = 1.0
    lang: str = "en-us"
    provider: str = "auto"
    cache_dir: Path | None = None
    synthesizer: Synthesizer | None = None

    def synthesize(self, text: str, output_path: Path) -> TTSResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.synthesizer is not None:
                self.synthesizer(text, output_path)
            else:
                self._synthesize_with_kokoro(text, output_path)
        except Exception as exc:
            return TTSResult(output_path=output_path, status="failed", error=str(exc))
        return TTSResult(output_path=output_path, status="ok", error=None)

    def _synthesize_with_kokoro(self, text: str, output_path: Path) -> None:
        import soundfile as sf

        kokoro = build_kokoro_cached(
            self.model_path,
            self.voices_path,
            provider=self.provider,
            cache_dir=self.cache_dir,
        )

        samples, sample_rate = kokoro.create(
            text,
            voice=self.voice,
            speed=self.speed,
            lang=self.lang,
        )

        sf.write(output_path, samples, sample_rate)
