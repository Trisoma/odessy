from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


STTRunner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class STTResult:
    text: str
    status: str
    error: str | None = None


@dataclass(frozen=True)
class WhisperCppSTT:
    whisper_bin: str
    model_path: str
    timeout: int = 60
    runner: STTRunner | None = None

    def transcribe(self, audio_path: Path) -> STTResult:
        argv = [
            self.whisper_bin,
            "-m",
            self.model_path,
            "-f",
            str(audio_path),
            "-ng",
        ]
        try:
            completed = (self.runner or _run)(argv, self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return STTResult(text="", status="failed", error=str(exc))
        output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
        if completed.returncode != 0:
            return STTResult(text="", status="failed", error=output.strip() or f"exit code {completed.returncode}")
        text = parse_whisper_transcript(output)
        if not text:
            return STTResult(text="", status="empty", error="Whisper produced no transcript text")
        return STTResult(text=text, status="ok", error=None)


def parse_whisper_transcript(output: str) -> str:
    lines: list[str] = []
    pattern = re.compile(r"^\s*\[[^\]]+-->\s*[^\]]+\]\s*(?P<text>.+?)\s*$")
    for line in output.splitlines():
        match = pattern.match(line)
        if match:
            text = match.group("text").strip()
            if text:
                lines.append(text)
    return " ".join(lines).strip()


def _run(argv: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
