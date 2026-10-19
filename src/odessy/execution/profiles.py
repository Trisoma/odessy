"""Profile argument construction for each same-world backend.

Reimplemented from ``dsh-sandbox-local/src/profiles.ts``. The bwrap and Seatbelt
builders are upstream's, verbatim. The Landlock builder emits the launcher's
documented flag vocabulary directly rather than calling a Node helper; the
grammar is pinned by ``native/system/docs/cli-contract.md``:

```text
landlock-run [--ro <path>]... [--rw <path>]... -- <argv>...
```

Every builder here is pure: policy in, argv fragment out. Nothing touches the
host, which is what makes the profiles directly testable and keeps them the one
place a grant is spelled.

Each builder refuses ``danger-full-access``. A confining profile for a mode that
requests no confinement is a caller error, and silently producing one would mean
a caller could ask for full access and receive confinement, or worse, believe the
reverse.
"""

from __future__ import annotations

from collections.abc import Sequence

from odessy.execution.contract import CONFINED_MODES, ExecutionPolicy
from odessy.execution.roots import writable_roots

__all__ = [
    "bwrap_command",
    "bwrap_profile_args",
    "landlock_command",
    "landlock_profile_args",
    "seatbelt_command",
    "seatbelt_profile_args",
]


def _require_confined(policy: ExecutionPolicy, backend: str) -> None:
    if policy.mode not in CONFINED_MODES:
        raise ValueError(
            f"{backend} was asked for a profile under mode {policy.mode!r}, which requests no "
            "confinement; only read-only and workspace-write have a profile"
        )


# --------------------------------------------------------------------------
# bwrap
# --------------------------------------------------------------------------


def bwrap_profile_args(policy: ExecutionPolicy) -> list[str]:
    """The bwrap mounts for one policy.

    A read-only host root, a *fresh* ``/dev``, a private PID namespace, and
    ``/proc`` mounted from it. The fresh ``/dev`` matters for more than hygiene:
    it shadows the host's device nodes, so a confined process cannot see a
    serial port, camera, or GPIO chip at all. Device access is therefore
    restricted by absence rather than by permission.

    ``workspace-write`` adds an ephemeral ``/tmp`` and a writable bind of the
    workspace root.
    """

    _require_confined(policy, "bwrap")

    args = [
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--unshare-pid",
        "--proc",
        "/proc",
        "--die-with-parent",
    ]
    if policy.mode == "workspace-write":
        args.extend(["--tmpfs", "/tmp"])
        args.extend(["--bind", policy.workspace_root, policy.workspace_root])
    return args


def bwrap_command(
    executable: str, argv: Sequence[str], policy: ExecutionPolicy
) -> list[str]:
    """The full argv to spawn: runner, profile, separator, caller's command."""

    return [executable, *bwrap_profile_args(policy), "--", *argv]


# --------------------------------------------------------------------------
# Landlock
# --------------------------------------------------------------------------


def landlock_profile_args(policy: ExecutionPolicy) -> list[str]:
    """The launcher grants for one policy.

    Landlock rulesets are allow-lists, so everything not granted is denied.
    ``--ro /`` grants read and execute across the host; ``--rw /dev/null`` is the
    sink the wrapped shell requires (a grant on a non-directory keeps only its
    file-compatible access bits, which is how that works).

    ``workspace-write`` additionally grants the temp area and the workspace root.
    """

    _require_confined(policy, "landlock")

    args = ["--ro", "/", "--rw", "/dev/null"]
    if policy.mode == "workspace-write":
        args.extend(["--rw", "/tmp", "--rw", policy.workspace_root])
    return args


def landlock_command(
    executable: str, argv: Sequence[str], policy: ExecutionPolicy
) -> list[str]:
    return [executable, *landlock_profile_args(policy), "--", *argv]


# --------------------------------------------------------------------------
# Seatbelt
# --------------------------------------------------------------------------


def _sbpl_string(path: str) -> str:
    """Quote one path as an SBPL string literal.

    Backslash first, then quote, matching upstream's ordering: escaping the
    quote first would corrupt any path already containing a backslash.
    """

    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def seatbelt_profile_args(policy: ExecutionPolicy) -> list[str]:
    """The ``sandbox-exec`` arguments for one policy.

    Allow-default with ``file-write*`` denied, then exactly the mode's promised
    write effects allowed back: the ``/dev/null`` sink and the canonical
    writable roots. The roots come from the shared
    :func:`odessy.execution.roots.writable_roots` helper so this profile and any
    other consumer of that helper cannot drift apart and produce a "the write
    tool cannot write there but the shell can" asymmetry.
    """

    _require_confined(policy, "seatbelt")

    forms = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        f"(allow file-write* (literal {_sbpl_string('/dev/null')}))",
    ]

    roots = writable_roots(policy)
    if roots:
        subpaths = " ".join(f"(subpath {_sbpl_string(root)})" for root in roots)
        forms.append(f"(allow file-write* {subpaths})")

    return ["-p", " ".join(forms)]


def seatbelt_command(
    executable: str, argv: Sequence[str], policy: ExecutionPolicy
) -> list[str]:
    return [executable, *seatbelt_profile_args(policy), "--", *argv]
