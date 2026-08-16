from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from odessy.workspace_authority import WorkspaceAuthority


class TerminalRunResult(TypedDict):
    stdout: str
    stderr: str
    exit_code: int
    duration: float
    status: str
    command: str
    cwd: str
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool


@dataclass(frozen=True)
class CommandBridge:
    """Workspace-bound command runner for the laptop execution environment."""

    workspace_root: Path
    max_timeout: int = 30
    max_stdout_chars: int = 64_000
    max_stderr_chars: int = 32_000

    def run(self, *, command: str, cwd: str = ".", timeout: int = 10) -> TerminalRunResult:
        resolved_cwd = self.resolve_cwd(cwd)
        effective_timeout = min(max(1, timeout), self.max_timeout)
        start = time.monotonic()
        process: subprocess.Popen[str] | None = None

        try:
            process = subprocess.Popen(
                _shell_command(command),
                cwd=resolved_cwd,
                env=build_sanitized_environment(os.environ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                encoding="utf-8",
                errors="replace",
                text=True,
                creationflags=_creation_flags(),
                start_new_session=os.name != "nt",
            )
            stdout, stderr = process.communicate(timeout=effective_timeout)
            duration = time.monotonic() - start
            stdout, stdout_truncated = _truncate_text(stdout, self.max_stdout_chars)
            stderr, stderr_truncated = _truncate_text(stderr, self.max_stderr_chars)
            exit_code = process.returncode if process.returncode is not None else -1
            status = "ok" if exit_code == 0 else "failed"
            return {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
                "duration": duration,
                "status": status,
                "command": command,
                "cwd": str(resolved_cwd),
                "timed_out": False,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            }
        except subprocess.TimeoutExpired as exc:
            if process is not None:
                _terminate_process_tree(process)
                stdout, stderr = process.communicate()
            else:
                stdout = exc.stdout or ""
                stderr = exc.stderr or ""
            duration = time.monotonic() - start
            stdout, stdout_truncated = _truncate_text(stdout or "", self.max_stdout_chars)
            stderr_text = stderr or f"Command timed out after {effective_timeout} seconds."
            stderr_text, stderr_truncated = _truncate_text(stderr_text, self.max_stderr_chars)
            return {
                "stdout": stdout,
                "stderr": stderr_text,
                "exit_code": -1,
                "duration": duration,
                "status": "timeout",
                "command": command,
                "cwd": str(resolved_cwd),
                "timed_out": True,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            }

    def resolve_cwd(self, cwd: str) -> Path:
        return WorkspaceAuthority(self.workspace_root).resolve_cwd(cwd)


def _shell_command(command: str) -> list[str]:
    if os.name == "nt":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["/bin/sh", "-lc", command]


def build_sanitized_environment(source: os._Environ[str]) -> dict[str, str]:
    allowed = {
        "COMSPEC",
        "LANG",
        "LC_ALL",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "WINDIR",
    }
    result: dict[str, str] = {}
    for key, value in source.items():
        upper = key.upper()
        if upper in allowed and not _looks_like_secret_env_name(upper):
            result[key] = value
    return result


def _looks_like_secret_env_name(name: str) -> bool:
    secret_terms = {
        "API_KEY",
        "AWS_",
        "AZURE_",
        "CREDENTIAL",
        "GITHUB_TOKEN",
        "HF_TOKEN",
        "HUGGINGFACE",
        "OPENAI_",
        "PASSWORD",
        "PRIVATE",
        "SECRET",
        "TOKEN",
    }
    return any(term in name for term in secret_terms)


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n[odessy: output truncated]\n", True


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
