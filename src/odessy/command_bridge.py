from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict


class TerminalRunResult(TypedDict):
    stdout: str
    stderr: str
    exit_code: int
    duration: float
    status: str
    command: str
    cwd: str
    timed_out: bool


@dataclass(frozen=True)
class CommandBridge:
    """Workspace-bound command runner for the laptop execution environment."""

    workspace_root: Path
    max_timeout: int = 30

    def run(self, *, command: str, cwd: str = ".", timeout: int = 10) -> TerminalRunResult:
        resolved_cwd = self.resolve_cwd(cwd)
        effective_timeout = min(max(1, timeout), self.max_timeout)
        start = time.monotonic()

        try:
            completed = subprocess.run(
                _shell_command(command),
                cwd=resolved_cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                check=False,
            )
            duration = time.monotonic() - start
            status = "ok" if completed.returncode == 0 else "failed"
            return {
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "exit_code": completed.returncode,
                "duration": duration,
                "status": status,
                "command": command,
                "cwd": str(resolved_cwd),
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start
            return {
                "stdout": exc.stdout or "",
                "stderr": exc.stderr or f"Command timed out after {effective_timeout} seconds.",
                "exit_code": -1,
                "duration": duration,
                "status": "timeout",
                "command": command,
                "cwd": str(resolved_cwd),
                "timed_out": True,
            }

    def resolve_cwd(self, cwd: str) -> Path:
        root = self.workspace_root.resolve()
        candidate = Path(cwd)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        if resolved != root and root not in resolved.parents:
            raise PermissionError("cwd is outside the allowed workspace")
        return resolved


def _shell_command(command: str) -> list[str]:
    if os.name == "nt":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/sh", "-lc", command]
