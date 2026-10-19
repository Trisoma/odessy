"""Root derivation and canonical path resolution.

The canonicalization behaviour is *pinned* rather than assumed. Node's JavaScript
``realpath`` and CPython's ``realpath`` do not share a history around ``..``
following a symlink, and a divergence there silently widens a writable root from
one directory to its parent.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from odessy.execution.contract import ExecutionPolicy, Mode
from odessy.execution.roots import canonical_path, writable_roots


def _policy(mode: Mode, workspace: Path) -> ExecutionPolicy:
    return ExecutionPolicy(mode=mode, workspace_root=str(workspace))


def _symlink_or_skip(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
        pytest.skip("symlinks are not available in this environment")


# --------------------------------------------------------------------------
# writable_roots
# --------------------------------------------------------------------------


def test_read_only_allows_no_writable_root(tmp_path: Path) -> None:
    assert writable_roots(_policy("read-only", tmp_path)) == []


def test_full_access_allows_no_writable_root(tmp_path: Path) -> None:
    """Anything that is not ``workspace-write`` yields nothing.

    ``danger-full-access`` means confinement is bypassed entirely, so there is no
    allow-list to derive; a caller must not read an empty list as "deny all".
    """

    assert writable_roots(_policy("danger-full-access", tmp_path)) == []


def test_workspace_write_includes_workspace_tmp_and_tempdir(tmp_path: Path) -> None:
    roots = writable_roots(_policy("workspace-write", tmp_path))

    assert roots[0] == canonical_path(str(tmp_path))
    assert canonical_path("/tmp") in roots
    assert canonical_path(tempfile.gettempdir()) in roots


def test_roots_are_deduplicated(tmp_path: Path) -> None:
    roots = writable_roots(_policy("workspace-write", tmp_path))

    assert len(roots) == len(set(roots)), "the allow-list must not repeat a root"


def test_workspace_root_is_first(tmp_path: Path) -> None:
    """Ordering matches upstream's insertion order, so the workspace root leads."""

    roots = writable_roots(_policy("workspace-write", tmp_path))

    assert roots[0] == canonical_path(str(tmp_path))


# --------------------------------------------------------------------------
# canonical_path
# --------------------------------------------------------------------------


def test_existing_path_is_resolved(tmp_path: Path) -> None:
    assert canonical_path(str(tmp_path)) == str(Path(tmp_path).resolve())


def test_missing_path_returns_its_spelling() -> None:
    """A root that does not exist matches nothing until it does.

    Returning the spelling is the conservative outcome; inventing a fallback
    would grant a path the caller never named.
    """

    missing = os.path.join(tempfile.gettempdir(), "odessy-does-not-exist-canonical")

    assert canonical_path(missing) == missing


def test_symlink_is_resolved_to_its_target(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    _symlink_or_skip(real, link)

    assert canonical_path(str(link)) == canonical_path(str(real))


def test_parent_traversal_after_a_symlink_resolves_component_wise(tmp_path: Path) -> None:
    """``link/..`` must resolve to the symlink target's parent.

    A lexically collapsing implementation removes ``..`` first and returns
    ``tmp_path``, which would turn a grant of one directory into a grant of the
    whole temporary tree. This is the exact behaviour ``canonicalPath`` exists to
    preserve, and the reason upstream uses Node's *native* realpath rather than
    its JavaScript one.
    """

    deep = tmp_path / "deep" / "nested" / "leaf"
    deep.mkdir(parents=True)
    link = tmp_path / "link"
    _symlink_or_skip(deep, link)

    resolved = canonical_path(str(link / ".."))

    assert resolved == canonical_path(str(tmp_path / "deep" / "nested")), (
        "canonical_path collapsed '..' lexically instead of resolving the symlink "
        "component first; this widens a writable root"
    )
    assert resolved != canonical_path(str(tmp_path))
