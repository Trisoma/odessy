"""End-of-utterance detection.

The session needs to know when the user has finished speaking, within three bounds
the plan fixes: wait at most 10 s for speech to start, end after 800 ms of silence,
and never record more than 20 s. A turn shorter than 300 ms of speech is not a
request, it is a cough, and submitting it would ask the model to act on noise.

This is a pure state machine over frames. Durations are counted in frames rather than
read from a clock, so a test that feeds 32 ms frames gets byte-identical decisions to
the device with no sleeping and no flakiness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from odessy.voice.frames import VAD_FRAME_MS

SegmenterState = Literal["waiting", "recording", "ended", "no_speech"]


@dataclass(frozen=True)
class SegmenterConfig:
    frame_ms: int = VAD_FRAME_MS
    #: Speech needed before a segment can count as a request.
    min_speech_ms: int = 300
    #: Trailing silence that ends the utterance.
    silence_end_ms: int = 800
    #: Hard ceiling on one utterance, silence included.
    max_utterance_ms: int = 20_000
    #: How long to wait for speech to begin after the ready cue.
    speech_start_timeout_ms: int = 10_000

    def __post_init__(self) -> None:
        if self.frame_ms < 1:
            raise ValueError("frame_ms must be positive")
        if self.min_speech_ms < 0 or self.silence_end_ms < 1:
            raise ValueError("silence_end_ms must be positive and min_speech_ms non-negative")


@dataclass
class UtteranceSegmenter:
    """Collect one utterance, or report that there was nothing to send."""

    config: SegmenterConfig = field(default_factory=SegmenterConfig)
    state: SegmenterState = "waiting"
    _utterance: bytearray = field(default_factory=bytearray, repr=False)
    _waiting_ms: int = 0
    _captured_ms: int = 0
    _speech_ms: int = 0
    _silence_run_ms: int = 0

    def begin(self) -> None:
        """Open a capture window. Any previous utterance is discarded."""
        self.state = "waiting"
        self._utterance.clear()
        self._waiting_ms = 0
        self._captured_ms = 0
        self._speech_ms = 0
        self._silence_run_ms = 0

    @property
    def utterance(self) -> bytes:
        return bytes(self._utterance)

    @property
    def speech_ms(self) -> int:
        return self._speech_ms

    def push(self, pcm: bytes, is_speech: bool) -> SegmenterState:
        """Feed one frame and return the state after it."""

        frame_ms = self.config.frame_ms

        if self.state == "waiting":
            if is_speech:
                self.state = "recording"
                self._utterance.extend(pcm)
                self._captured_ms += frame_ms
                self._speech_ms += frame_ms
                self._silence_run_ms = 0
                return self.state
            self._waiting_ms += frame_ms
            if self._waiting_ms >= self.config.speech_start_timeout_ms:
                self.state = "no_speech"
            return self.state

        if self.state != "recording":
            return self.state

        self._utterance.extend(pcm)
        self._captured_ms += frame_ms
        if is_speech:
            self._speech_ms += frame_ms
            self._silence_run_ms = 0
        else:
            self._silence_run_ms += frame_ms

        if self._captured_ms >= self.config.max_utterance_ms:
            self.state = "ended" if self._speech_ms >= self.config.min_speech_ms else "no_speech"
            return self.state
        if self._silence_run_ms >= self.config.silence_end_ms:
            self.state = "ended" if self._speech_ms >= self.config.min_speech_ms else "no_speech"
        return self.state
