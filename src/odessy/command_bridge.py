from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from odessy.output_retention import TextRetainer, format_retention_notice
from odessy.redaction import looks_like_secret_name, redact_text
from odessy.spill import SpillStore
from odessy.workspace_authority import WorkspaceAuthority


class _TerminalRunResultOptional(TypedDict, total=False):
    """Locator fields added by the spill integration.

    Kept in a `total=False` base so callers that build a `TerminalRunResult`
    literal without them still type-check, and so adding a locator never breaks
    an existing consumer.
    """

    stdout_spill: dict[str, Any] | None
    stderr_spill: dict[str, Any] | None


class TerminalRunResult(_TerminalRunResultOptional):
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
    #: Spill backend for withheld output. Built lazily from `workspace_root` when
    #: not supplied, so existing constructions keep working unchanged.
    spill_store: SpillStore | None = None

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
            stdout, stdout_truncated, stdout_spill = self._prepare_output(
                stdout, label="stdout", max_chars=self.max_stdout_chars
            )
            stderr, stderr_truncated, stderr_spill = self._prepare_output(
                stderr, label="stderr", max_chars=self.max_stderr_chars
            )
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
                "stdout_spill": stdout_spill,
                "stderr_spill": stderr_spill,
            }
        except subprocess.TimeoutExpired as exc:
            if process is not None:
                _terminate_process_tree(process)
                stdout, stderr = process.communicate()
            else:
                stdout = exc.stdout or ""
                stderr = exc.stderr or ""
            duration = time.monotonic() - start
            stdout, stdout_truncated, stdout_spill = self._prepare_output(
                stdout or "", label="stdout", max_chars=self.max_stdout_chars
            )
            stderr_text = stderr or f"Command timed out after {effective_timeout} seconds."
            stderr_text, stderr_truncated, stderr_spill = self._prepare_output(
                stderr_text, label="stderr", max_chars=self.max_stderr_chars
            )
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
                "stdout_spill": stdout_spill,
                "stderr_spill": stderr_spill,
            }

    def resolve_cwd(self, cwd: str) -> Path:
        return WorkspaceAuthority(self.workspace_root).resolve_cwd(cwd)

    def _spill_store(self) -> SpillStore:
        if self.spill_store is not None:
            return self.spill_store
        return SpillStore(workspace_root=self.workspace_root)

    def _prepare_output(
        self, text: str, *, label: str, max_chars: int
    ) -> tuple[str, bool, dict[str, Any] | None]:
        """Redact, bound to two ends, and spill the remainder.

        Order is not negotiable:

        1. Redact FIRST. A secret must never reach the spill file, and once the
           file exists on disk it cannot be recalled. Redacting only the
           model-facing text would leave the credential persisted.
        2. Retain head-and-tail. The tail carries build and test failures, which
           the old head-only cut discarded precisely when it mattered.
        3. Spill the full text, and if that fails keep the ORIGINAL.

        The failure branch deliberately returns untruncated content. A storage
        failure must not become silent data loss; the cost is a large
        observation, and that cost is the caller's to weigh, not this method's to
        hide.
        """

        redacted = redact_text(text)
        tail_budget = int(max_chars * _TAIL_SHARE)
        retainer = TextRetainer(
            head_bytes=max(0, max_chars - tail_budget), tail_bytes=tail_budget
        )
        result = retainer.retain(redacted)
        if not result.truncated:
            return redacted, False, None

        locator = self._spill_store().save_text(redacted, label=label)
        if locator is None:
            # Storage failed. Preserve everything rather than truncate.
            return redacted, False, None

        notice = format_retention_notice(
            result,
            unit_label=label,
            recovery=f"Full output: {locator.relative_path}",
        )
        return f"{result.text}\n{notice}\n", True, locator.as_dict()


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
    """Screening predicate for the sanitized child environment.

    Delegates to the shared vocabulary in `redaction.py` so the environment
    boundary and the model-facing boundary cannot drift apart.
    """

    return looks_like_secret_name(name)


#: Fraction of the inline budget kept from the END of the output. Terminal errors
#: and build failures appear at the tail, so the tail gets the larger share when
#: both ends cannot be kept.
_TAIL_SHARE = 0.6


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Legacy head-only truncation.

    Retained so pre-spill callers and tests keep their exact behaviour. New code
    should use `CommandBridge._prepare_output`, which retains both ends and
    spills the remainder instead of discarding it.
    """

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
