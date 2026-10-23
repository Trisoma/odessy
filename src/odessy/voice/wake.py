"""Wake-word detection and the gate in front of it.

The detector answers one question per frame: how strongly does this audio look like
the configured phrase? The gate turns that stream of scores into **accepted
activations**, because a raw score above threshold fires on several consecutive
frames of the same word and would open capture repeatedly.

Nothing here decides anything about authority. An activation opens the microphone
for a request; the request still goes through the same validation, policy, and
confirmation as typed input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from odessy.voice.frames import WAKE_FRAME_BYTES, WAKE_FRAME_MS

#: Default score above which a frame counts as the wake word.
DEFAULT_THRESHOLD = 0.5
#: Activations are ignored for this long after an accepted one.
DEBOUNCE_MS = 1000


class WakeDetector(Protocol):
    frame_bytes: int

    def score(self, pcm: bytes) -> float:
        ...

    def reset(self) -> None:
        """Drop buffered model state after a transition.

        openWakeWord keeps a rolling audio window; without this, audio from before
        the cue is scored as if it were spoken after it.
        """


@dataclass(frozen=True)
class WakeGate:
    """Threshold plus debounce, in frame counts so behaviour is reproducible.

    A test that feeds 80 ms frames gets exactly the same decisions as the device,
    with no wall-clock dependency.
    """

    threshold: float = DEFAULT_THRESHOLD
    debounce_ms: int = DEBOUNCE_MS
    frame_ms: int = WAKE_FRAME_MS

    def __post_init__(self) -> None:
        if not 0.0 < self.threshold <= 1.0:
            raise ValueError("wake threshold must be greater than 0 and at most 1")
        if self.debounce_ms < 0:
            raise ValueError("wake debounce must not be negative")

    @property
    def debounce_frames(self) -> int:
        return self.debounce_ms // self.frame_ms

    def accepts(self, score: float, *, frames_since_activation: int | None) -> bool:
        """Whether this score starts a new command window.

        ``frames_since_activation`` is ``None`` when nothing has been accepted yet,
        which is why the caller keeps the state rather than the gate.
        """

        if score < self.threshold:
            return False
        return frames_since_activation is None or frames_since_activation >= self.debounce_frames


class OpenWakeWordDetector:
    """openWakeWord behind a lazy import.

    ``openwakeword`` is an optional dependency: importing ``odessy.voice`` on a host
    that only does text must not require it. The import therefore happens on first
    use, and a missing install raises a message that says how to fix it rather than
    a bare ImportError.

    The bundled pretrained models are licensed CC BY-NC-SA 4.0 (not Apache 2.0, which
    covers the code). They are never shipped in this repository: ``odessy voice
    --setup-wake`` fetches them explicitly, and the model path here points at whatever
    the user installed.
    """

    def __init__(self, model_path: str, *, frame_bytes: int = WAKE_FRAME_BYTES,
                 inference_framework: str = "onnx", threshold: float = DEFAULT_THRESHOLD) -> None:
        self.frame_bytes = frame_bytes
        self._threshold = threshold
        try:
            import numpy
            from openwakeword.model import Model
        except ImportError as error:  # pragma: no cover - depends on the host
            raise RuntimeError(
                "wake-word support needs openwakeword: pip install -e '.[wake]', "
                "then run `odessy voice --setup-wake` to fetch the model files"
            ) from error
        self._numpy = numpy
        # inference_framework is passed through rather than guessed: the ONNX runtime
        # is already a dependency for Kokoro, and tflite is not. On macOS ARM64 the
        # onnx embedding model scores near zero, so the framework is a setting.
        self._model = Model(wakeword_models=[model_path], inference_framework=inference_framework)

    def score(self, pcm: bytes) -> float:
        frame = self._numpy.frombuffer(pcm, dtype=self._numpy.int16)
        predictions = self._model.predict(frame)
        if not isinstance(predictions, dict) or not predictions:
            return 0.0
        return float(max(predictions.values()))

    def reset(self) -> None:
        """Clear buffered audio and scores after a transition."""
        reset = getattr(self._model, "reset", None)
        if callable(reset):
            reset()
            return
        # Older releases have no `predict`-level reset; clearing the keyword buffers
        # is what stops pre-cue audio from scoring as post-cue audio.
        for name in ("preprocessor", "model"):
            component = getattr(self._model, name, None)
            buffers = getattr(component, "__dict__", None)
            if not isinstance(buffers, dict):
                continue
            for value in buffers.values():
                reset_buffer = getattr(value, "reset", None)
                if callable(reset_buffer):
                    reset_buffer()
