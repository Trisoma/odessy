from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .onnx_runtime import build_kokoro

Synthesizer = Callable[[str, Path], None]

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

        kokoro = build_kokoro(
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
