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


#: Recognized non-speech markers only; unknown bracketed text can be a request.
_ANNOTATION = re.compile(
    r"\[\s*(?:BLANK_AUDIO|SILENCE|MUSIC|INAUDIBLE)\s*\]"
    r"|\(\s*(?:BLANK_AUDIO|SILENCE|MUSIC|INAUDIBLE)\s*\)",
    re.IGNORECASE,
)


def blank_transcript_reason(text: str) -> str | None:
    """Why this transcript carries no usable speech, or ``None`` when it does.

    Whisper can return a marker for silence, most often ``[BLANK_AUDIO]``, as well
    as an empty transcript. A marker is non-empty text, so a
    status check alone passes it through, and the model is asked to act on a silence
    marker -- proposing a tool for a request nobody made. Nothing in the runtime was
    wrong; the transcript was never speech.

    Only the known markers above are discarded. Unknown bracketed text and words
    outside a known marker are preserved, so ``[inaudible] hello`` and ``[restart
    the server]`` remain requests. Punctuation-only text is also blank.
    """

    stripped = text.strip()
    if not stripped:
        return "transcript was empty"
    remainder = _ANNOTATION.sub("", stripped)
    if remainder.strip(" \t.,!?;:-—…\"'"):
        return None
    if not _ANNOTATION.search(stripped):
        return f"transcript was punctuation only: {stripped[:40]!r}"
    return f"transcript was non-speech annotation only: {stripped[:40]!r}"


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
