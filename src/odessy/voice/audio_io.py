from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

class AudioIOError(RuntimeError):
    pass

@dataclass(frozen=True)
class CommandAudioIO:
    recorder: str = "arecord"
    player: str = "aplay"
    input_device: str | None = None
    output_device: str | None = None
    sample_rate: int = 16_000
    _processes: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    _process_lock: object = field(default_factory=threading.Lock, init=False, repr=False, compare=False)
    _operation: object = field(default_factory=threading.local, init=False, repr=False, compare=False)

    def run_cancellable(self, function, args, cancelled):
        self._operation.cancelled = cancelled
        try:
            if cancelled.is_set():
                raise AudioIOError('Audio operation cancelled')
            return function(*args)
        finally:
            del self._operation.cancelled

    def stop(self) -> None:
        """Terminate only recorder/player processes created by this adapter."""
        with self._process_lock:
            processes = tuple(self._processes.values())
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)

    def _run(self, argv, *, timeout, action):
        process = None
        try:
            with self._process_lock:
                cancelled = getattr(self._operation, 'cancelled', None)
                if cancelled is not None and cancelled.is_set():
                    raise AudioIOError('Audio operation cancelled')
                process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', errors='replace')
                self._processes[id(process)] = process
                if cancelled is not None and cancelled.is_set():
                    process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise
            if process.returncode:
                raise AudioIOError(f'Audio {action} failed with exit code {process.returncode}: {(stderr or stdout or "").strip()}')
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AudioIOError(f'Audio {action} failed: {exc}') from exc
        finally:
            if process is not None:
                with self._process_lock:
                    self._processes.pop(id(process), None)

    def record(self, output_path: Path, seconds: int) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        argv = [self.recorder]
        if self.input_device:
            argv.extend(["-D", self.input_device])
        argv.extend(["-f", "S16_LE", "-r", str(self.sample_rate), "-c", "1", "-d", str(seconds), str(output_path)])
        self._run(argv, timeout=seconds + 10, action="record")

    def play(self, audio_path: Path) -> None:
        argv = [self.player]
        if self.output_device:
            argv.extend(["-D", self.output_device])
        argv.append(str(audio_path))
        self._run(argv, timeout=120, action="play")

def _run_audio_command(argv: list[str], *, timeout: int, action: str) -> None:
    try:
        result = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioIOError(f"Audio {action} failed: {exc}") from exc
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise AudioIOError(f"Audio {action} failed with exit code {result.returncode}: {stderr}")
