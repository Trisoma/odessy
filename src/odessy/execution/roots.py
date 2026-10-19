"""Canonical path resolution and the writable-root derivation.

Reimplemented for Python from ``dsh-sandbox/src/roots.ts``. The behaviour is
deliberately identical; only the language differs.

``workspace-write`` means "the workspace root plus the platform temp areas", and
this module is that meaning's one home. Every enforcement dialect that expresses
a mode as a canonical allow-list derives its list here, so an asymmetry such as
"the write tool cannot write to the temp area but the shell can" cannot arise
between two consumers that both call this.
"""

from __future__ import annotations

import errno
import os
import tempfile

from odessy.execution.contract import ExecutionPolicy

__all__ = ["canonical_path", "writable_roots"]

#: Bound on symlink expansion, mirroring the kernel's own limit for ELOOP.
_MAX_SYMLINK_HOPS = 40


def _strip_extended_prefix(path: str) -> str:
    """Normalise a Windows extended-length path back to its ordinary spelling.

    On Windows ``os.readlink`` returns an absolute symlink target in the
    extended-length form. That names the same directory, but as a different
    string, which would defeat the canonical equality this module exists to
    establish. A no-op everywhere else.
    """

    if os.name != "nt":
        return path

    extended = os.sep * 2 + "?" + os.sep
    unc = extended + "UNC" + os.sep
    if path.startswith(unc):
        return os.sep * 2 + path[len(unc) :]
    if path.startswith(extended):
        return path[len(extended) :]
    return path


def _split_components(path: str) -> list[str]:
    """Split a path into components, normalising the separator first."""

    for separator in (os.sep, os.altsep):
        if separator:
            path = path.replace(separator, os.sep)
    return path.split(os.sep)


def _resolve_component_wise(path: str) -> str:
    """Resolve symlinks component by component.

    ``os.path.realpath`` is **not** usable here. On Windows it normalises the
    path lexically before resolving, so it removes a ``..`` that follows a
    symlink without ever expanding that symlink. ``link/..`` then resolves to
    the symlink's own parent instead of the parent of its target, which silently
    widens a grant by one directory level. This was found empirically by
    ``tests/test_execution_roots.py``, not assumed.
    """

    if not os.path.isabs(path):
        path = os.path.join(os.getcwd(), path)

    drive, remainder = os.path.splitdrive(path)
    resolved: list[str] = []
    pending: list[str] = _split_components(remainder)
    hops = 0

    while pending:
        name = pending.pop(0)
        if name in ("", os.curdir):
            continue
        if name == os.pardir:
            # `..` applies to what has ALREADY been resolved. That is the whole
            # point: it must never remove an unresolved component, because an
            # unresolved component may be a symlink to a different depth.
            if resolved:
                resolved.pop()
            continue

        candidate = drive + os.sep + os.sep.join(resolved + [name])
        try:
            target = os.readlink(candidate)
        except OSError:
            # Not a symlink, or does not exist. Keep the component as spelled;
            # a missing root matches nothing until it exists.
            resolved.append(name)
            continue

        target = _strip_extended_prefix(target)
        hops += 1
        if hops > _MAX_SYMLINK_HOPS:
            raise OSError(errno.ELOOP, "too many levels of symbolic links", path)

        if os.path.isabs(target):
            drive, remainder = os.path.splitdrive(target)
            resolved = []
            pending = _split_components(remainder) + pending
        else:
            pending = _split_components(target) + pending

    anchor = drive + os.sep
    return anchor + os.sep.join(resolved) if resolved else anchor


def canonical_path(path: str) -> str:
    """Resolve a granted root to the path the enforcement layer actually compares.

    Canonical, because path-matching enforcement layers match resolved paths:
    ``/tmp`` *is* ``/private/tmp`` on macOS, and an as-spelled grant would match
    nothing.

    A path that does not exist returns its spelling as-is, matching Node's
    ``realpathSync.native``, which throws in that case and is caught upstream.
    Returning the spelling is the conservative outcome: a root that does not
    exist matches nothing until it does, and inventing a fallback would grant a
    path the caller never named.

    The resolution itself is component-wise rather than delegated to
    ``os.path.realpath``; see :func:`_resolve_component_wise` for why.
    """

    if not os.path.exists(path):
        return path
    try:
        return _resolve_component_wise(path)
    except OSError:
        return path


def writable_roots(policy: ExecutionPolicy) -> list[str]:
    """The roots one confined execution may *write* under.

    ``read-only`` allows nothing. ``workspace-write`` allows the policy's
    workspace root, the host ``/tmp``, and the platform temp directory. The temp
    directory is included because it is the real temp area for ``mkstemp``-family
    tools; omitting it would deny what the mode promises.

    Canonicalized and then deduplicated in first-seen order, matching upstream's
    ``[...new Set([...].map(canonicalPath))]``. That dedup is what collapses
    ``/tmp`` and ``/private/tmp`` to one entry on macOS.
    """
    if policy.mode != "workspace-write":
        return []

    deduplicated: dict[str, None] = {}
    for candidate in (policy.workspace_root, "/tmp", tempfile.gettempdir()):
        deduplicated.setdefault(canonical_path(candidate), None)
    return list(deduplicated)
