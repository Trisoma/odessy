from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class AudioIOError(RuntimeError):
    pass


Runner = subprocess.run


@dataclass(frozen=True)
class CommandAudioIO:
    recorder: str = "arecord"
    player: str = "aplay"
    input_device: str | None = None
    output_device: str | None = None
    sample_rate: int = 16_000

    def record(self, output_path: Path, seconds: int) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        argv = [self.recorder]
        if self.input_device:
            argv.extend(["-D", self.input_device])
        argv.extend(["-f", "S16_LE", "-r", str(self.sample_rate), "-c", "1", "-d", str(seconds), str(output_path)])
        _run_audio_command(argv, timeout=seconds + 10, action="record")

    def play(self, audio_path: Path) -> None:
        argv = [self.player]
        if self.output_device:
            argv.extend(["-D", self.output_device])
        argv.append(str(audio_path))
        _run_audio_command(argv, timeout=120, action="play")


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
