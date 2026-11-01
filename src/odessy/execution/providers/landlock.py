"""The Landlock backend: a kernel-enforced ruleset installed by a launcher.

This is the provider where the ABI decides what is achievable, so the limitation
set is *derived* from the negotiated mask rather than declared.

The ABI boundaries below are taken from the kernel header as quoted in the
launcher's C source, which spells out which bit each ABI introduced:

```text
LL_FS_REFER       (1 << 13)   ABI 2
LL_FS_TRUNCATE    (1 << 14)   ABI 3   (ABI 4 added TCP bits only)
LL_FS_IOCTL_DEV   (1 << 15)   ABI 5
```

A right absent from the negotiated mask is not governed at all, which is what
makes each limitation a statement about this host rather than boilerplate. The
consequence of ``cannot_deny_truncate`` is concrete: before ABI 3, ``truncate``
is not mediated, so a process under ``read-only`` can still truncate a file it
has filesystem write permission for.

The launcher is probed for, not shipped. Odessy reimplements the profile the
launcher accepts but does not vendor the native binary, so this backend reports
itself unavailable when no launcher is on ``PATH``. That is the honest answer:
the ABI being present does not confine anything on its own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from odessy.execution.contract import (
    ExecutionPolicy,
    Limitation,
    RunnerFailureRule,
)
from odessy.execution.probe import HostFacts, discover_host_facts
from odessy.execution.profiles import landlock_command
from odessy.execution.providers.base import ConfiningProvider

__all__ = ["LandlockProvider"]


class LandlockProvider(ConfiningProvider):
    """Confines through a Landlock ruleset installed by ``landlock-run``."""

    provider_id = "landlock"

    #: The ABI that introduced each filesystem right, from the kernel header.
    REFER_ABI = 2
    TRUNCATE_ABI = 3
    IOCTL_DEV_ABI = 5

    #: Rights an older kernel does not govern at all. Their absence is a
    #: shortfall in the file effects the mode promises, so it makes the
    #: enforcement partial rather than merely being noted.
    GAP_LIMITATIONS = frozenset(
        {
            "cannot_deny_truncate",
            "cannot_restrict_rename_between_dirs",
            "cannot_restrict_ioctl",
        }
    )

    DENIAL_SIGNATURES = ("permission denied",)

    RUNNER_FAILURE_RULES = (
        RunnerFailureRule(
            fatal_signatures=("landlock-run: ",),
            # A successfully exec'd child may itself return 125, so the exit code
            # alone is never evidence: both signals are required.
            allowed_exit_codes=(125,),
            informational_lines=("landlock-run: partial enforcement (older Landlock ABI)",),
        ),
    )

    def __init__(
        self,
        *,
        facts: HostFacts | None = None,
        launcher: str | None = None,
    ) -> None:
        self._facts = facts
        self._launcher = launcher

    def _host(self) -> HostFacts:
        return self._facts if self._facts is not None else discover_host_facts()

    def usable(self) -> tuple[bool, str]:
        facts = self._host()
        if facts.landlock.abi is None:
            return False, facts.landlock.detail
        launcher = facts.landlock_launcher
        if launcher.path is None or launcher.enforcement is None:
            return False, launcher.detail
        return True, launcher.detail

    def limitations(self, policy: ExecutionPolicy) -> frozenset[Limitation]:
        abi = self._host().landlock.abi

        # Scope notes: properties of the grants this provider emits, true at
        # every ABI, and outside what the file-effect vocabulary covers.
        limitations: set[Limitation] = {
            # `--ro /` grants read across the host, so reads are unrestricted.
            "cannot_restrict_reads",
            # No network right is requested, and none exists in this mask.
            "cannot_restrict_network",
            # The filesystem mask has no process-visibility right at all.
            "cannot_restrict_process_visibility",
            "host_kernel_shared",
        }
        # `--ro /` cannot carve a path back out, so hidden paths stay visible.
        if policy.hidden_paths:
            limitations.add("cannot_hide_paths")

        # Gaps: rights the negotiated ABI does not govern.
        if abi is None or abi < self.TRUNCATE_ABI:
            limitations.add("cannot_deny_truncate")
        if abi is None or abi < self.REFER_ABI:
            limitations.add("cannot_restrict_rename_between_dirs")
        if abi is None or abi < self.IOCTL_DEV_ABI:
            # `--ro /` makes device nodes openable for read, and an unmediated
            # ioctl is how a serial or GPIO device is actually driven.
            limitations.add("cannot_restrict_ioctl")

        return frozenset(limitations)

    def wrap(self, argv: Sequence[str], policy: ExecutionPolicy) -> Sequence[str]:
        executable = self._launcher or self._host().landlock_launcher.path
        if executable is None:  # pragma: no cover - usable() gates this
            raise ValueError("landlock is usable but has no launcher path")
        return landlock_command(executable, argv, policy)

    def probe_facts(self) -> Mapping[str, object]:
        facts = self._host()
        return {
            "abi": facts.landlock.abi,
            "abi_detail": facts.landlock.detail,
            "launcher": facts.landlock_launcher.path,
            "launcher_enforcement": facts.landlock_launcher.enforcement,
        }

    def describe(self, policy: ExecutionPolicy) -> str:
        facts = self._host()
        return (
            f"Landlock ABI {facts.landlock.abi} with --ro / and the mode's writable "
            f"grants; the launcher reports {facts.landlock_launcher.enforcement} enforcement"
        )
