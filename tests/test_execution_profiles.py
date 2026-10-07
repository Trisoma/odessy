"""Profile argument construction.

The profiles are pure, so they are pinned here exactly: a grant spelled wrong is
a boundary that does not exist, and the argv is the only thing the runner sees.
"""

from __future__ import annotations

import pytest

from odessy.execution.contract import ExecutionPolicy
from odessy.execution.profiles import (
    bwrap_command,
    bwrap_profile_args,
    landlock_command,
    landlock_profile_args,
    seatbelt_command,
    seatbelt_profile_args,
)


def _policy(mode: str, root: str = "/work") -> ExecutionPolicy:
    return ExecutionPolicy(mode=mode, workspace_root=root)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Every profile refuses a mode that requests no confinement
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build",
    [bwrap_profile_args, landlock_profile_args, seatbelt_profile_args],
)
def test_full_access_has_no_profile(build) -> None:
    """Producing a confining profile for ``danger-full-access`` would be a lie.

    A caller asking for no confinement must not silently receive one, and a
    caller asking for confinement must not silently receive none.
    """

    with pytest.raises(ValueError, match="requests no confinement"):
        build(_policy("danger-full-access"))


# --------------------------------------------------------------------------
# bwrap
# --------------------------------------------------------------------------


def test_bwrap_read_only_binds_the_host_root_read_only() -> None:
    args = bwrap_profile_args(_policy("read-only"))

    assert args[:4] == ["--ro-bind", "/", "/", "--dev"]
    assert "--die-with-parent" in args


def test_bwrap_read_only_does_not_grant_a_writable_workspace() -> None:
    args = bwrap_profile_args(_policy("read-only"))

    assert "--bind" not in args
    assert "--tmpfs" not in args
    assert "/work" not in args


def test_bwrap_read_only_uses_a_fresh_dev_and_a_private_pid_namespace() -> None:
    """These two are why bwrap restricts more than the file-effect vocabulary.

    A fresh ``/dev`` shadows the host's device nodes, so a serial port or camera
    is invisible rather than merely unwritable; ``--unshare-pid`` hides host
    processes.
    """

    args = bwrap_profile_args(_policy("read-only"))

    assert args[args.index("--dev") + 1] == "/dev"
    assert "--unshare-pid" in args
    assert args[args.index("--proc") + 1] == "/proc"


def test_bwrap_workspace_write_adds_an_ephemeral_tmp_and_the_workspace() -> None:
    args = bwrap_profile_args(_policy("workspace-write", "/work"))

    assert args[args.index("--tmpfs") + 1] == "/tmp"
    assert args[args.index("--bind") + 1 : args.index("--bind") + 3] == ["/work", "/work"]


def test_bwrap_command_places_the_separator_before_the_caller_command() -> None:
    argv = bwrap_command("/usr/bin/bwrap", ["echo", "hi"], _policy("read-only"))

    assert argv[0] == "/usr/bin/bwrap"
    assert argv[argv.index("--") + 1 :] == ["echo", "hi"]


# --------------------------------------------------------------------------
# Landlock
# --------------------------------------------------------------------------


def test_landlock_read_only_grants_the_root_and_only_the_null_sink() -> None:
    args = landlock_profile_args(_policy("read-only"))

    assert args == ["--ro", "/", "--rw", "/dev/null"]


def test_landlock_workspace_write_grants_tmp_and_the_workspace() -> None:
    args = landlock_profile_args(_policy("workspace-write", "/work"))

    assert args == ["--ro", "/", "--rw", "/dev/null", "--rw", "/tmp", "--rw", "/work"]


def test_landlock_never_grants_a_read_write_root() -> None:
    """``--ro /`` is the whole point: everything not granted is denied.

    Landlock rulesets are allow-lists, so a stray ``--rw /`` would lift the
    confinement entirely.
    """

    for mode in ("read-only", "workspace-write"):
        args = landlock_profile_args(_policy(mode))
        for index, token in enumerate(args):
            if token == "--rw":
                assert args[index + 1] != "/"


def test_landlock_command_places_the_separator_before_the_caller_command() -> None:
    argv = landlock_command("/usr/bin/landlock-run", ["echo", "hi"], _policy("read-only"))

    assert argv[0] == "/usr/bin/landlock-run"
    assert argv[argv.index("--") + 1 :] == ["echo", "hi"]


# --------------------------------------------------------------------------
# Seatbelt
# --------------------------------------------------------------------------


def test_seatbelt_read_only_denies_writes_and_allows_only_the_sink() -> None:
    args = seatbelt_profile_args(_policy("read-only"))
    profile = args[args.index("-p") + 1]

    assert args[0] == "-p"
    assert "(deny file-write*)" in profile
    assert '(allow file-write* (literal "/dev/null"))' in profile
    assert "subpath" not in profile, "read-only must not allow a writable root"


def test_seatbelt_workspace_write_allows_the_shared_writable_roots() -> None:
    args = seatbelt_profile_args(_policy("workspace-write", "/work"))
    profile = args[args.index("-p") + 1]

    assert "subpath" in profile
    assert '"/work"' in profile


def test_seatbelt_escapes_backslashes_before_quotes() -> None:
    """Escaping the quote first would corrupt a path containing a backslash."""

    args = seatbelt_profile_args(
        ExecutionPolicy(mode="workspace-write", workspace_root='/we"ird\\path')
    )
    profile = args[args.index("-p") + 1]

    assert '\\"' in profile, "the embedded quote must be escaped"
    assert "\\\\" in profile, "the embedded backslash must be escaped"


def test_seatbelt_command_places_the_separator_before_the_caller_command() -> None:
    argv = seatbelt_command("/usr/bin/sandbox-exec", ["echo", "hi"], _policy("read-only"))

    assert argv[0] == "/usr/bin/sandbox-exec"
    assert argv[argv.index("--") + 1 :] == ["echo", "hi"]
