"""List and remove the standing ACEs the windows-acl runner leaves on a workspace.

The runner grants a workspace-derived capability SID (``S-1-4-<a>-<b>``) write
access on the workspace and leaves it in place as a cross-session cache; a later
read-only run is unaffected, because a write-restricted token only uses what its
restricting list carries. Renaming a workspace derives a new SID and leaves the old
ACE behind as inert residue. ``python -m odessy sandbox cleanup`` lists these ACEs
and, with ``--apply``, removes them; the next confined write grants afresh.

Only two-subauthority ``S-1-4-x-y`` SIDs are touched: the runner's workspace SIDs.
Its per-run temp SIDs have a third subauthority and are removed by the runner.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

__all__ = ["list_workspace_aces", "remove_workspace_aces", "main"]

_WORKSPACE_SID = re.compile(r"\*?(S-1-4-\d+-\d+)(?![-\d])")


def _icacls(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["icacls", *arguments], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)


def list_workspace_aces(workspace: Path) -> list[str]:
    """The runner's workspace SIDs with an ACE on ``workspace`` itself."""

    if os.name != "nt":
        return []
    result = _icacls(str(workspace))
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or result.stdout.strip() or f"icacls exited with {result.returncode}")
    return sorted(set(_WORKSPACE_SID.findall(result.stdout)))


def remove_workspace_aces(workspace: Path, sids: list[str]) -> list[str]:
    """Remove each SID's ACEs from the workspace tree; returns the failures."""

    failures = []
    for sid in sids:
        result = _icacls(str(workspace), "/remove:g", f"*{sid}", "/T", "/C", "/Q")
        if result.returncode != 0:
            failures.append(f"{sid}: {result.stderr.strip() or result.stdout.strip()}")
    return failures


def main(workspace: Path, *, apply: bool) -> int:
    if os.name != "nt":
        print("Nothing to clean: the windows-acl runner leaves ACEs on Windows only.")
        return 0
    try:
        sids = list_workspace_aces(workspace)
    except OSError as error:
        print(f"Cannot read the ACL of {workspace}: {error}")
        return 1
    if not sids:
        print(f"No windows-acl workspace ACEs on {workspace}.")
        return 0
    print(f"windows-acl workspace ACEs on {workspace}: {', '.join(sids)}")
    if not apply:
        print("Run again with --apply to remove them. The next confined write grants afresh.")
        return 0
    failures = remove_workspace_aces(workspace, sids)
    for failure in failures:
        print(f"Could not remove {failure}")
    if not failures:
        print("Removed.")
    return 1 if failures else 0
