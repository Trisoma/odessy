"""Fixed-size PCM framing for the voice session.

Two models in this pipeline want different frame sizes: openWakeWord scores 80 ms
chunks, and the Silero VAD inside it wants 32 ms. One microphone stream has to feed
both, so bytes are rebuffered per consumer instead of re-recording.

Frame sizes are expressed as byte counts on purpose. Everything here is 16 kHz, mono,
signed 16-bit little-endian, which is what ``arecord -f S16_LE -r 16000 -c 1`` emits
and what both models expect; a length in milliseconds converts through
:data:`BYTES_PER_MS`.
"""

from __future__ import annotations

from dataclasses import dataclass

SAMPLE_RATE = 16_000
CHANNELS = 1
BYTES_PER_SAMPLE = 2
#: Bytes of audio in one millisecond.
BYTES_PER_MS = SAMPLE_RATE * BYTES_PER_SAMPLE // 1000

#: openWakeWord's native chunk.
WAKE_FRAME_MS = 80
WAKE_FRAME_BYTES = WAKE_FRAME_MS * BYTES_PER_MS

#: Silero VAD's native chunk (32 ms, 512 samples).
VAD_FRAME_MS = 32
VAD_FRAME_BYTES = VAD_FRAME_MS * BYTES_PER_MS


@dataclass(frozen=True)
class AudioFrame:
    """One chunk of capture, with the monotonic index it arrived at."""

    pcm: bytes
    index: int

    @property
    def duration_ms(self) -> int:
        return len(self.pcm) // BYTES_PER_MS


class FrameBuffer:
    """Re-cut a byte stream into fixed-size frames, carrying the remainder.

    80 ms does not divide evenly into 32 ms, so a consumer with a different frame
    size cannot simply share the reader's chunks. The carry keeps every byte and
    never asks the source for a partial frame.
    """

    def __init__(self, frame_bytes: int) -> None:
        if frame_bytes <= 0:
            raise ValueError("frame_bytes must be positive")
        self._frame_bytes = frame_bytes
        self._buffer = bytearray()

    @property
    def frame_bytes(self) -> int:
        return self._frame_bytes

    def push(self, chunk: bytes) -> list[bytes]:
        self._buffer.extend(chunk)
        frames: list[bytes] = []
        while len(self._buffer) >= self._frame_bytes:
            frames.append(bytes(self._buffer[: self._frame_bytes]))
            del self._buffer[: self._frame_bytes]
        return frames

    def reset(self) -> None:
        self._buffer.clear()


class FrameQueue:
    """A bounded queue of frames.

    Overflow is a failure, not a backlog: if the consumer cannot keep up, the audio
    that is dropped is unknown audio, and acting on a spliced recording is worse than
    refusing the request. ``push`` therefore reports the overflow instead of evicting.
    """

    def __init__(self, max_frames: int) -> None:
        if max_frames < 1:
            raise ValueError("max_frames must be at least 1")
        self._max_frames = max_frames
        self._frames: list[AudioFrame] = []
        self.overflowed = False

    def __len__(self) -> int:
        return len(self._frames)

    def push(self, frame: AudioFrame) -> None:
        if len(self._frames) >= self._max_frames:
            self.overflowed = True
            return
        self._frames.append(frame)

    def pop_all(self) -> list[AudioFrame]:
        frames, self._frames = self._frames, []
        return frames

    def clear(self) -> None:
        self._frames.clear()
        self.overflowed = False
