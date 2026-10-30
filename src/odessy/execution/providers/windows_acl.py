"""The Windows backend for the Python side: the Node tool server's windows-acl runner.

The confinement itself (a ``WRITE_RESTRICTED`` token whose write capability is an
ACL ACE on the workspace) lives in the Node runner, ``tools/src/execution/win32``.
Rather than port that FFI to Python, this provider builds the same argv the Node
``WindowsAclProvider`` builds and lets the runner do the work, so single-machine
mode on Windows gets the same partial boundary whether or not the Node tool server
is the one running the command.

It is ``partial`` for the same two structural reasons as the Node provider: a
write-restricted token must keep ``Everyone`` in its restricting lists, and NTFS
hard links alias a file object rather than a path. Reads, network and process
visibility are not governed, so hidden paths stay visible and the network stays
open; both are reported. Extra writable folders are not granted (the runner grants
the workspace and its own temp folder only), so a command writing to a package
cache fails rather than writing outside the boundary.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from odessy.execution.contract import ExecutionPolicy, Limitation, RunnerFailureRule
from odessy.execution.providers.base import ConfiningProvider

__all__ = ["WindowsAclProvider", "windows_acl_runner"]


def windows_acl_runner() -> Path | None:
    """The built Node runner, or None when the tool server is not installed and built."""

    from odessy.tools.node_mcp import _node_tools_dir, _public_repo_root

    try:
        tools = _node_tools_dir(_public_repo_root())
    except ValueError:
        return None
    runner = tools / "dist" / "src" / "execution" / "win32" / "runner.js"
    koffi = tools / "node_modules" / "koffi"
    return runner if runner.is_file() and koffi.is_dir() else None


class WindowsAclProvider(ConfiningProvider):
    """Confines through the Node windows-acl runner."""

    provider_id = "windows-acl"

    GAP_LIMITATIONS = frozenset({"reliance_on_everyone_ace", "cannot_restrict_hardlink_alias"})

    DENIAL_SIGNATURES = (
        "access is denied",
        "access to the path",
        "permission denied",
        "operation not permitted",
    )

    RUNNER_FAILURE_RULES = (
        RunnerFailureRule(fatal_signatures=("windows-acl-run: ",), allowed_exit_codes=(127,)),
    )

    def __init__(self, *, node: str | None = None, runner: Path | None = None, platform: str | None = None) -> None:
        self._node = node
        self._runner = runner
        self._platform = platform

    def _parts(self) -> tuple[str | None, Path | None]:
        return self._node or shutil.which("node"), self._runner or windows_acl_runner()

    def usable(self) -> tuple[bool, str]:
        if (self._platform or os.name) != "nt":
            return False, "windows-acl runs on Windows only"
        node, runner = self._parts()
        if node is None:
            return False, "node is not on PATH; install Node.js to use the windows-acl runner"
        if runner is None:
            return False, "the Node tool server is not installed and built (tools/dist/src/execution/win32/runner.js)"
        return True, f"windows-acl runner {runner}"

    def limitations(self, policy: ExecutionPolicy) -> frozenset[Limitation]:
        limitations: set[Limitation] = {
            "cannot_restrict_reads",
            "cannot_restrict_network",
            "cannot_restrict_process_visibility",
            "no_console_isolation",
            "host_kernel_shared",
            *self.GAP_LIMITATIONS,
        }
        if policy.hidden_paths:
            limitations.add("cannot_hide_paths")
        return frozenset(limitations)

    def wrap(self, argv: Sequence[str], policy: ExecutionPolicy) -> Sequence[str]:
        node, runner = self._parts()
        if node is None or runner is None:  # pragma: no cover - usable() gates this
            raise ValueError("windows-acl is usable but has no runner")
        temp = tempfile.gettempdir()
        if policy.mode == "workspace-write":
            workspace = os.path.normcase(os.path.abspath(policy.workspace_root))
            if os.path.normcase(os.path.abspath(temp)).startswith(workspace + os.sep):
                raise ValueError("the temp folder is inside the workspace; the windows-acl runner refuses that overlap")
        return [node, str(runner), "--workspace", policy.workspace_root, "--temp", temp, "--mode", policy.mode, "--", *argv]

    def probe_facts(self) -> Mapping[str, object]:
        node, runner = self._parts()
        return {"node": node, "runner": str(runner) if runner else None}

    def describe(self, policy: ExecutionPolicy) -> str:
        return (
            "a WRITE_RESTRICTED token whose write capability is an ACL ACE on the workspace "
            "(the Node windows-acl runner); reads, network and process visibility are unrestricted"
        )
