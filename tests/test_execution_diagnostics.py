"""Runner failure versus command denial.

Each backend speaks its own failure and denial dialect. These tests pin that a
*broken runner* is never reported as a *denied command*, because the two demand
opposite responses: one is an infrastructure fault, the other is confinement
working exactly as intended.
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from odessy.execution.contract import RunnerFailureRule
from odessy.execution.diagnostics import (
    classify_runner_failure,
    is_runner_spawn_failure,
    matches_signature,
)

# --------------------------------------------------------------------------
# Real backend dialects, as the providers will declare them.
# --------------------------------------------------------------------------

_BWRAP = RunnerFailureRule(fatal_signatures=("bwrap: ",))

_LANDLOCK = RunnerFailureRule(
    fatal_signatures=("landlock-run: ",),
    allowed_exit_codes=(125,),
    informational_lines=("landlock-run: partial enforcement (older Landlock ABI)",),
)

_SEATBELT = RunnerFailureRule(fatal_signatures=("sandbox-exec: ",))

_WINDOWS_ACL = RunnerFailureRule(
    fatal_signatures=("windows-acl-run: ",),
    allowed_exit_codes=(127,),
)


# --------------------------------------------------------------------------
# classify_runner_failure
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rules", [(_BWRAP,), (_LANDLOCK,), (_SEATBELT,), (_WINDOWS_ACL,)])
def test_a_zero_exit_is_never_a_runner_failure(rules: tuple[RunnerFailureRule, ...]) -> None:
    assert classify_runner_failure(0, "bwrap: anything\n", rules) is None


@pytest.mark.parametrize("rules", [(_BWRAP,), (_LANDLOCK,), (_SEATBELT,), (_WINDOWS_ACL,)])
def test_a_signal_termination_is_never_a_runner_failure(rules: tuple[RunnerFailureRule, ...]) -> None:
    """``None`` means the process was signalled, not that the runner failed."""

    assert classify_runner_failure(None, "bwrap: anything\n", rules) is None


def test_bwrap_fatal_signature_is_matched_and_reported() -> None:
    match = classify_runner_failure(1, "bwrap: Creating new namespace failed\n", (_BWRAP,))

    assert match is not None
    assert match.detail == "bwrap: Creating new namespace failed"


def test_matching_is_case_insensitive() -> None:
    match = classify_runner_failure(1, "BWRAP: Failed\n", (_BWRAP,))

    assert match is not None


def test_exit_code_gate_excludes_other_codes() -> None:
    """Landlock's rule only fires on its own failure exit code."""

    assert classify_runner_failure(1, "landlock-run: boom\n", (_LANDLOCK,)) is None
    assert classify_runner_failure(125, "landlock-run: boom\n", (_LANDLOCK,)) is not None


def test_informational_line_is_excluded_before_fatal_matching() -> None:
    """The older-ABI notice contains the fatal prefix but is benign.

    Without exact-line exclusion this line would be classified as a runner
    failure and a working sandbox would be reported as broken.
    """

    stderr = "landlock-run: partial enforcement (older Landlock ABI)\n"

    assert classify_runner_failure(125, stderr, (_LANDLOCK,)) is None


def test_informational_exclusion_is_exact_not_substring() -> None:
    """A line that merely *contains* the notice is still fatal evidence."""

    stderr = "landlock-run: partial enforcement (older Landlock ABI) and then failed\n"

    assert classify_runner_failure(125, stderr, (_LANDLOCK,)) is not None


def test_windows_acl_dialect() -> None:
    match = classify_runner_failure(127, "windows-acl-run: bad args\n", (_WINDOWS_ACL,))

    assert match is not None
    assert classify_runner_failure(1, "windows-acl-run: bad args\n", (_WINDOWS_ACL,)) is None


def test_blank_fatal_signature_is_ignored() -> None:
    """A whitespace-only signature is not evidence, and must not match everything."""

    rules = (RunnerFailureRule(fatal_signatures=("  ",)),)

    assert classify_runner_failure(1, "anything at all\n", rules) is None


def test_blank_signature_does_not_disable_a_valid_sibling() -> None:
    rules = (RunnerFailureRule(fatal_signatures=("   ", "bwrap: ")),)

    assert classify_runner_failure(1, "bwrap: boom\n", rules) is not None


def test_no_rules_means_no_runner_failure() -> None:
    assert classify_runner_failure(1, "bwrap: boom\n", ()) is None


# --------------------------------------------------------------------------
# matches_signature
# --------------------------------------------------------------------------


def test_matches_signature_requires_a_nonzero_exit() -> None:
    assert matches_signature(0, "read-only file system", ("read-only file system",)) is False
    assert matches_signature(None, "read-only file system", ("read-only file system",)) is False


def test_matches_signature_is_case_insensitive() -> None:
    assert matches_signature(1, "Read-Only File System", ("read-only file system",)) is True


def test_matches_signature_rejects_an_unrelated_dialect() -> None:
    """The selected backend's dialect only, never a cross-backend union.

    Landlock reports EACCES text; a bwrap denial reports EROFS text. Matching
    against a union would claim denials this backend never produces.
    """

    assert matches_signature(1, "permission denied", ("read-only file system",)) is False


def test_matches_signature_accepts_every_signature_in_the_selected_dialect() -> None:
    windows_acl_dialect = (
        "access is denied",
        "access to the path",
        "permission denied",
        "operation not permitted",
    )

    for text in windows_acl_dialect:
        assert matches_signature(1, f"error: {text}", windows_acl_dialect) is True


# --------------------------------------------------------------------------
# is_runner_spawn_failure
# --------------------------------------------------------------------------


def test_spawn_failure_needs_a_usable_workdir(tmp_path: Path) -> None:
    """An unusable cwd produces the same errno as a missing executable.

    The workdir is therefore ruled out independently before anything is
    attributed to the runner.
    """

    missing_dir = tmp_path / "gone"
    error = FileNotFoundError(errno.ENOENT, "No such file or directory", "/usr/bin/bwrap")

    assert is_runner_spawn_failure(error, "/usr/bin/bwrap", str(missing_dir)) is False


def test_spawn_failure_attributes_a_matching_path(tmp_path: Path) -> None:
    error = FileNotFoundError(errno.ENOENT, "No such file or directory", "/usr/bin/bwrap")

    assert is_runner_spawn_failure(error, "/usr/bin/bwrap", str(tmp_path)) is True


def test_spawn_failure_rejects_a_path_that_is_not_the_runner(tmp_path: Path) -> None:
    error = FileNotFoundError(errno.ENOENT, "No such file or directory", "/etc/passwd")

    assert is_runner_spawn_failure(error, "/usr/bin/bwrap", str(tmp_path)) is False


def test_spawn_failure_without_a_path_is_not_attributed(tmp_path: Path) -> None:
    """No path evidence means no attribution.

    Python exposes no syscall string to fall back on, so the conservative answer
    is ``False``. The failure surfaces as unclassified, which is honest, rather
    than being reported as a runner failure (or as a denial).
    """

    error = FileNotFoundError(errno.ENOENT, "No such file or directory")

    assert is_runner_spawn_failure(error, "/usr/bin/bwrap", str(tmp_path)) is False


def test_spawn_failure_accepts_permission_denied(tmp_path: Path) -> None:
    error = PermissionError(errno.EACCES, "Permission denied", "/usr/bin/bwrap")

    assert is_runner_spawn_failure(error, "/usr/bin/bwrap", str(tmp_path)) is True


def test_spawn_failure_ignores_an_unrelated_errno(tmp_path: Path) -> None:
    error = OSError(errno.EINVAL, "Invalid argument", "/usr/bin/bwrap")

    assert is_runner_spawn_failure(error, "/usr/bin/bwrap", str(tmp_path)) is False


def test_spawn_failure_ignores_unrelated_error_types(tmp_path: Path) -> None:
    assert is_runner_spawn_failure(RuntimeError("boom"), "/usr/bin/bwrap", str(tmp_path)) is False


def test_spawn_failure_needs_a_runner_program(tmp_path: Path) -> None:
    error = FileNotFoundError(errno.ENOENT, "No such file or directory", "/usr/bin/bwrap")

    assert is_runner_spawn_failure(error, None, str(tmp_path)) is False
