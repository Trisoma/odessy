"""Speech detection for utterance endpointing.

Endpointing answers "has the user finished speaking?", which is a different question
from wake detection. A wake score says a phrase was heard once; this says speech is
still arriving. It is also the only thing that can end a follow-up turn in
conversation mode, where there is no wake phrase to wait for.

openWakeWord ships Silero VAD, so the preferred detector reuses it rather than adding
another model runtime. The energy detector exists so the segmentation logic can be
tested, and so a host with no VAD model degrades to something predictable instead of
recording silence until the ceiling.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from typing import Any, Protocol

from odessy.voice.frames import VAD_FRAME_BYTES

#: Speech probability above which a 32 ms frame counts as speech.
DEFAULT_VAD_THRESHOLD = 0.5

#: RMS above which an energy detector calls a frame speech. A quiet room sits well
#: below this on a headset microphone; it is a floor, not a voice detector.
DEFAULT_ENERGY_THRESHOLD = 500.0


class SpeechDetector(Protocol):
    frame_bytes: int

    def is_speech(self, pcm: bytes) -> bool:
        ...

    def reset(self) -> None:
        ...


@dataclass
class EnergySpeechDetector:
    """Frame energy above a floor. Cheap, deterministic, and honest about its limits."""

    threshold: float = DEFAULT_ENERGY_THRESHOLD
    frame_bytes: int = VAD_FRAME_BYTES

    def is_speech(self, pcm: bytes) -> bool:
        samples = array("h")
        samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
        if not samples:
            return False
        total = sum(sample * sample for sample in samples)
        return (total / len(samples)) ** 0.5 >= self.threshold

    def reset(self) -> None:
        return None


class SileroSpeechDetector:
    """openWakeWord's bundled Silero VAD, behind a lazy import."""

    def __init__(self, *, frame_bytes: int = VAD_FRAME_BYTES,
                 threshold: float = DEFAULT_VAD_THRESHOLD) -> None:
        self.frame_bytes = frame_bytes
        self._threshold = threshold
        try:
            import numpy
            from openwakeword.vad import VAD
        except ImportError as error:  # pragma: no cover - depends on the host
            raise RuntimeError(
                "speech detection needs openwakeword: pip install -e '.[wake]'"
            ) from error
        self._numpy = numpy
        self._vad: Any = VAD()

    def is_speech(self, pcm: bytes) -> bool:
        frame = self._numpy.frombuffer(pcm, dtype=self._numpy.int16)
        # openWakeWord defaults to 480, splitting our 512 samples into 480 + 32;
        # the deployed Silero model requires 512-sample inference chunks.
        return float(self._vad.predict(frame, frame_size=512)) >= self._threshold

    def reset(self) -> None:
        reset = getattr(self._vad, "reset", None)
        if callable(reset):
            reset()
