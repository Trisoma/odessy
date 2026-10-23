"""Microphone capture and speaker playback as owned devices.

The voice session keeps **one** long-lived capture process for the whole run. A
wake word has to be heard while a reply is playing and while a turn is executing, so
the recorder cannot be a bounded per-command ``arecord -d N``: the device is already
open when the user starts speaking.

Playback is a separate process so it can be terminated. Interrupting a reply means
stopping a process that is already writing to the speakers; there is no polite way to
ask ``aplay`` to finish early.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from threading import Lock
from pathlib import Path
from typing import Protocol

from odessy.voice.frames import SAMPLE_RATE


class AudioStreamError(RuntimeError):
    """The capture process could not start, or stopped unexpectedly."""


class AudioStream(Protocol):
    """A pull-based source of raw PCM bytes: 16 kHz, mono, signed 16-bit."""

    def read(self, size: int) -> bytes:
        """Block until ``size`` bytes are available, or return fewer at end of stream."""

    def close(self) -> None:
        ...


class AudioPlayer(Protocol):
    def play(self, audio_path: Path) -> None:
        ...

    def stop(self) -> None:
        ...


@dataclass
class CommandAudioStream:
    """A single ``arecord`` process that owns the microphone.

    ``read`` is blocking, so callers must run it off the event loop
    (``asyncio.to_thread``); that is what lets wake detection keep running while a
    turn executes.
    """

    recorder: str = "arecord"
    input_device: str | None = None
    sample_rate: int = SAMPLE_RATE
    _process: subprocess.Popen[bytes] | None = None

    def start(self) -> "CommandAudioStream":
        argv = [self.recorder]
        if self.input_device:
            argv.extend(["-D", self.input_device])
        # `-t raw` and no `-d`: an open-ended stream of PCM instead of a file with a
        # header. The session decides when to stop listening, not the recorder.
        argv.extend(
            [
                "-f",
                "S16_LE",
                "-r",
                str(self.sample_rate),
                "-c",
                "1",
                "-t",
                "raw",
                "-q",
            ]
        )
        try:
            self._process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except OSError as error:
            raise AudioStreamError(f"could not start {self.recorder}: {error}") from error
        if self._process.stdout is None:  # pragma: no cover - PIPE guarantees this
            raise AudioStreamError(f"{self.recorder} produced no readable stream")
        return self

    def read(self, size: int) -> bytes:
        if self._process is None or self._process.stdout is None:
            raise AudioStreamError("capture is not running")
        chunk = self._process.stdout.read(size)
        if not chunk:
            raise AudioStreamError("the recorder stopped producing audio")
        return chunk

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - unresponsive device
            process.kill()
            process.wait()


@dataclass
class CommandPlayer:
    """Own playback across cancellation, queued jobs, and slow process startup."""

    player: str = "aplay"
    output_device: str | None = None
    _process: subprocess.Popen[bytes] | None = field(default=None, init=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)
    _generation: int = field(default=0, init=False)
    _sequence: int = field(default=0, init=False)

    @property
    def generation(self) -> int:
        """Snapshot before scheduling work that may outlive cancellation."""
        with self._lock:
            return self._generation

    def play(self, audio_path: Path, *, generation: int | None = None) -> None:
        with self._lock:
            if generation is not None and generation != self._generation:
                return
            if generation is None:
                self._generation += 1
            generation = self._generation
            self._sequence += 1
            sequence = self._sequence
            previous, self._process = self._process, None
        self._terminate(previous)
        argv = [self.player]
        if self.output_device:
            argv.extend(["-D", self.output_device])
        argv.append(str(audio_path))
        # Popen can block. Never hold the state lock across process creation:
        # stop must invalidate a startup even before a process handle exists.
        try:
            process = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except OSError as error:
            raise AudioStreamError(f"could not start {self.player}: {error}") from error
        with self._lock:
            current = generation == self._generation and sequence == self._sequence
            if current:
                self._process = process
        if not current:
            self._terminate(process)
            return
        try:
            process.wait(timeout=300)
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            self._sequence += 1
            process, self._process = self._process, None
        self._terminate(process)

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes] | None) -> None:
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - unresponsive device
            process.kill()
            process.wait()


def recorder_available(recorder: str = "arecord") -> bool:
    return shutil.which(recorder) is not None


def player_available(player: str = "aplay") -> bool:
    return shutil.which(player) is not None
