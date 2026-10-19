from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypedDict

from odessy.execution.contract import (
    ConfineResult,
    ExecutionPolicy,
    ExecutionProvider,
    IsolationRequirement,
    Mode,
    ProviderUnavailableError,
    Requirement,
)
from odessy.execution.providers import UnconfinedProvider
from odessy.output_retention import TextRetainer, format_retention_notice
from odessy.redaction import looks_like_secret_name, redact_text
from odessy.spill import SpillStore
from odessy.workspace_authority import WorkspaceAuthority


class _TerminalRunResultOptional(TypedDict, total=False):
    """Fields added after the original result contract, all optional.

    Kept in a `total=False` base so callers that build a `TerminalRunResult`
    literal without them still type-check, and so adding one never breaks an
    existing consumer.
    """

    stdout_spill: dict[str, Any] | None
    stderr_spill: dict[str, Any] | None
    #: What confinement actually achieved for this run. Always present, because a
    #: caller cannot tell a confined run from an unconfined one without it.
    sandbox: dict[str, Any]


class TerminalRunResult(_TerminalRunResultOptional):
    stdout: str
    stderr: str
    #: The process's own exit status, or `None` when there is none to report.
    #:
    #: `timed_out` is what distinguishes the cases. When the executor terminated the
    #: process, the code a killed process happens to report (1 on Windows) is an
    #: artifact of the kill rather than an outcome of the command, so reporting it
    #: would be semantically misleading. `None` says exactly "no process exit code
    #: exists"; a sentinel such as `-1` is indistinguishable from a real code and so
    #: reads as the command's own result.
    #:
    #: A negative value IS meaningful on POSIX -- it is `-signal` for a process killed
    #: by a signal -- so the executor preserves it rather than flattening it to `None`.
    #:
    #: A spawn failure does not produce a result at all: it raises.
    exit_code: int | None
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
    #: The confinement backend every command is routed through. `None` means no
    #: backend was configured, and that absence is REPORTED in the result rather
    #: than left for the caller to infer. A caller that wants a boundary injects a
    #: provider together with the policy it confines under.
    provider: ExecutionProvider | None = None
    #: The file-effect policy `provider` confines under. Which mode an action
    #: resolves to is the authority layer's decision, not this bridge's, so this is
    #: supplied rather than derived here.
    policy: ExecutionPolicy | None = None

    def __post_init__(self) -> None:
        if (self.provider is None) != (self.policy is None):
            raise ValueError(
                "CommandBridge requires provider and policy together: a policy "
                "without a backend confines nothing, and a backend without a "
                "policy has no mode to confine under"
            )

    def run(
        self,
        *,
        command: str,
        cwd: str = ".",
        timeout: int = 10,
        mode: Mode | None = None,
        isolation: IsolationRequirement | None = None,
    ) -> TerminalRunResult:
        resolved_cwd = self.resolve_cwd(cwd)
        effective_timeout = min(max(1, timeout), self.max_timeout)
        # Confinement happens before anything is spawned, and it raises rather than
        # falling back: a caller that asked for a boundary must never receive an
        # unconfined run instead. `ProviderUnavailableError`, the
        # `danger-full-access` refusal, and a policy that no backend satisfies all
        # therefore propagate out of `run()`.
        argv, sandbox = self._confine(_shell_command(command), mode, isolation)
        start = time.monotonic()
        process: subprocess.Popen[str] | None = None

        try:
            process = subprocess.Popen(
                argv,
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
            exit_code = process.returncode
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
                "sandbox": sandbox,
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
                # Decision (2026-10-19): a command Odessy terminated reports no exit
                # code. A native kill code describes executor termination, not the
                # command's own outcome. Raw Windows termination and failure codes
                # can coincide; POSIX signal codes are platform-specific. Keep the
                # same contract on both: None, with `timed_out` and `status` stating
                # why no command exit code is available.
                # See LIMITATION.md, "A Killed Command Reports No Exit Code".
                "exit_code": None,
                "duration": duration,
                "status": "timeout",
                "command": command,
                "cwd": str(resolved_cwd),
                "timed_out": True,
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "stdout_spill": stdout_spill,
                "stderr_spill": stderr_spill,
                "sandbox": sandbox,
            }

    def resolve_cwd(self, cwd: str) -> Path:
        return WorkspaceAuthority(self.workspace_root).resolve_cwd(cwd)

    def _confine(
        self,
        argv: list[str],
        mode: Mode | None = None,
        isolation: IsolationRequirement | None = None,
    ) -> tuple[list[str], dict[str, Any]]:
        """Route the argv through the configured backend.

        ``mode`` is the mode the *authority layer* resolved for this one call, and
        ``isolation`` is how strict it was about that. They are supplied per call rather
        than read off ``self.policy`` because a decision taken per action cannot live in a
        per-bridge field. ``None`` keeps the bridge's own policy, which is what a caller
        that has not resolved one gets.

        With no backend configured this still calls `confine()` -- through the named
        unconfined provider -- so every run states its own enforcement instead of
        silently reporting nothing. The inert mode passed in that case is
        `read-only`, chosen because it is the most restrictive value: should a
        backend ever be configured without an explicit policy, the default must
        fail closed rather than over-grant.
        """

        if self.provider is None:
            # `__post_init__` pairs the two fields, so the policy is None here too.
            result = _UNCONFINED.confine(
                argv,
                ExecutionPolicy(mode="read-only", workspace_root=str(self.workspace_root)),
            )
            return list(result.argv), _sandbox_report(result)

        assert self.policy is not None  # `__post_init__` pairs the two fields.
        policy = self.policy if mode is None else replace(self.policy, mode=mode)

        if isolation == "required":
            # This side both decides and executes, so there is no trust boundary to
            # cross -- but a `required` demand still has to be answered. Satisfying it
            # with a partial or absent backend here would be the same silent downgrade
            # the executor refuses on the other side of the boundary.
            evaluation = self.provider.evaluate(Requirement(policy=policy, isolation="required"))
            if not evaluation.satisfied:
                # (mode, detail), not a single message: the first argument is the mode the
                # backend could not satisfy, and the detail explains why.
                raise ProviderUnavailableError(
                    policy.mode,
                    f"required isolation is not met: {self.provider.provider_id} reports "
                    f"{evaluation.enforcement} enforcement ({evaluation.reason})",
                )

        result = self.provider.confine(argv, policy)
        return list(result.argv), _sandbox_report(result)

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


#: The named absence of a backend. Used when no provider was configured, so that
#: every command is still routed through `confine()` and the result always states
#: its own enforcement. The absence is loud, never concealed.
_UNCONFINED = UnconfinedProvider()


def _sandbox_report(result: ConfineResult) -> dict[str, Any]:
    """The confinement facts a caller needs, in the contract's own vocabulary.

    `provider_id` matters as much as `enforcement`: "unconfined" and "confined,
    but only partially" are different answers, and a caller that cannot tell them
    apart cannot act on either.
    """

    return {
        "provider_id": result.provider_id,
        "enforcement": result.enforcement,
        "limitations": sorted(result.limitations),
    }


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
        # Preserve an explicitly configured module search path. Reconstructing it
        # in a stripped environment can trigger unrelated installed-module scans.
        "PSMODULEPATH",
        # Let the host choose a writable module-analysis cache. Cache reuse alone
        # does not bound module discovery; preserve PSMODULEPATH above as well.
        "PSMODULEANALYSISCACHEPATH",
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
