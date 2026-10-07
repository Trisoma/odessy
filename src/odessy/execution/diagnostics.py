"""Runner-failure versus command-denial classification.

Reimplemented for Python from ``dsh-sandbox/src/diagnostics.ts``. The *behaviour*
is shared with the TypeScript side; the error shapes are not, because Node and
Python report spawn failures differently.

The distinction this module exists to preserve:

```text
runner failure   the command never ran, because the confinement runner failed
denial           confinement worked and blocked the command
```

Conflating them turns a broken sandbox into a confusing command error, and a
command error into apparent evidence that confinement worked. Every backend
reports both, and neither is inferred from exit status alone.
"""

from __future__ import annotations

import errno
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass

from odessy.execution.contract import RunnerFailureRule

__all__ = [
    "RunnerFailureMatch",
    "classify_runner_failure",
    "is_runner_spawn_failure",
    "matches_signature",
]

#: Errnos that can indicate the runner executable itself failed to start.
_SPAWN_ERRNOS = frozenset({errno.EACCES, errno.ENOENT})

_LINE_SPLIT = re.compile(r"\r?\n")


def _is_usable_workdir(path: str) -> bool:
    """Whether the caller-owned spawn working directory can be entered."""

    try:
        if not os.path.isdir(path):
            return False
        return os.access(path, os.X_OK)
    except OSError:
        return False


def is_runner_spawn_failure(
    error: BaseException,
    runner_program: str | None,
    workdir: str,
) -> bool:
    """Whether a spawn rejection is evidence that the *runner* failed to start.

    The working directory is checked independently, because an unusable working
    directory produces the same errno as a missing executable. Only a failure
    whose error path names ``runner_program`` is attributed to the runner.

    Unlike Node, Python exposes no ``syscall`` string, so when no error path
    matches there is no evidence at all and the answer is ``False``. That is the
    conservative direction: an unattributed failure must not be reported as a
    runner failure, and must not be reported as a denial either. It surfaces as
    an unclassified failure, which is honest.

    The working directory is checked at classification time rather than atomically
    with the spawn. Concurrent path replacement may therefore change attribution,
    but it cannot cause an unconfined execution.
    """

    if runner_program is None or not _is_usable_workdir(workdir):
        return False
    if not isinstance(error, (FileNotFoundError, PermissionError)):
        return False
    if getattr(error, "errno", None) not in _SPAWN_ERRNOS:
        return False

    for attribute in ("filename", "filename2"):
        if getattr(error, attribute, None) == runner_program:
            return True
    return False


@dataclass(frozen=True, slots=True)
class RunnerFailureMatch:
    """Fatal runner evidence, retained so the caller can report the detail."""

    detail: str


def classify_runner_failure(
    exit_code: int | None,
    stderr: str,
    rules: Sequence[RunnerFailureRule],
) -> RunnerFailureMatch | None:
    """Classify a settled process against the backend's structured failure rules.

    Each rule requires a non-zero exit, its optional exit-code gate, and a fatal
    signature on one stderr line *after* exact informational lines are excluded.
    Exit status alone never proves runner failure.
    """

    if exit_code is None or exit_code == 0:
        return None

    lines = _LINE_SPLIT.split(stderr)
    for rule in rules:
        if rule.allowed_exit_codes is not None and exit_code not in rule.allowed_exit_codes:
            continue
        informational = {line.lower() for line in rule.informational_lines}
        # An empty or whitespace-only signature is not meaningful evidence. Drop
        # it while keeping any valid signatures beside it active.
        fatal = [signature.lower() for signature in rule.fatal_signatures if signature.strip()]
        for line in lines:
            lowered = line.lower()
            if lowered in informational:
                continue
            if any(signature in lowered for signature in fatal):
                return RunnerFailureMatch(detail=line)
    return None


def matches_signature(
    exit_code: int | None,
    stderr: str,
    signatures: Sequence[str],
) -> bool:
    """Whether a non-zero exit's stderr matches one of the backend's deny signatures.

    ``signatures`` is the *selected backend's* dialect, never a cross-backend
    union: a union claims denials a given backend never produces.
    """

    if exit_code is None or exit_code == 0:
        return False
    lowered = stderr.lower()
    return any(signature.lower() in lowered for signature in signatures)
