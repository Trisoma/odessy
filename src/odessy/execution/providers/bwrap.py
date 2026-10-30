"""The bwrap backend: namespaces plus a read-only host root.

bwrap is available on Linux and confines by constructing namespaces rather than
by asking the kernel for a per-process ruleset. Its profile is also the strongest
of the three with respect to devices: ``--dev /dev`` mounts a *fresh* minimal
devtmpfs over ``/dev``, so the host's device nodes — serial ports, cameras, GPIO
chips — are not merely unwritable but invisible.

That is worth stating plainly because it is the reason this provider reports no
device limitation while a Landlock profile does: the restriction comes from
absence rather than from permission.

Limitations are derived from the profile this provider actually emits:

- ``--ro-bind / /`` grants read across the host, so reads are not restricted.
- No network namespace is unshared, so network is not restricted.
- ``--unshare-pid`` *does* restrict host process visibility, so that is genuinely
  not a limitation here.
- The host kernel is still shared, which is what makes this same-world
  confinement rather than isolation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from odessy.execution.contract import ExecutionPolicy, Limitation
from odessy.execution.probe import HostFacts, discover_host_facts
from odessy.execution.profiles import bwrap_command
from odessy.execution.providers.base import ConfiningProvider

__all__ = ["BwrapProvider"]

_USABLE = frozenset({"available_userns", "available_setuid_root"})


class BwrapProvider(ConfiningProvider):
    """Confines through bwrap's namespace and bind-mount profile."""

    provider_id = "bwrap"

    #: bwrap governs every file effect the modes promise, so nothing here is a
    #: shortfall. The entries returned by :meth:`limitations` are scope notes.
    GAP_LIMITATIONS = frozenset()

    DENIAL_SIGNATURES = ("read-only file system",)

    def __init__(
        self,
        *,
        facts: HostFacts | None = None,
        executable: str | None = None,
    ) -> None:
        self._facts = facts
        self._executable = executable

    def _host(self) -> HostFacts:
        return self._facts if self._facts is not None else discover_host_facts()

    def usable(self) -> tuple[bool, str]:
        bwrap = self._host().bwrap
        return bwrap.availability in _USABLE, bwrap.detail

    def limitations(self, policy: ExecutionPolicy) -> frozenset[Limitation]:
        # Reads stay unrestricted outside the hidden paths, which bwrap does hide;
        # the network is cut when the policy asks (`--unshare-net`).
        limitations: set[Limitation] = {"cannot_restrict_reads", "host_kernel_shared"}
        if policy.network != "deny":
            limitations.add("cannot_restrict_network")
        return frozenset(limitations)

    def wrap(self, argv: Sequence[str], policy: ExecutionPolicy) -> Sequence[str]:
        executable = self._executable or self._host().bwrap.path
        if executable is None:  # pragma: no cover - usable() gates this
            raise ValueError("bwrap is usable but has no executable path")
        return bwrap_command(executable, argv, policy)

    def probe_facts(self) -> Mapping[str, object]:
        bwrap = self._host().bwrap
        return {
            "availability": bwrap.availability,
            "path": bwrap.path,
            "is_setuid_root": bwrap.is_setuid_root,
            "version": bwrap.version,
        }

    def describe(self, policy: ExecutionPolicy) -> str:
        bwrap = self._host().bwrap
        return (
            "bwrap namespaces with a read-only host root, a fresh /dev, and a private "
            f"PID namespace ({bwrap.availability})"
        )
