"""The macOS Seatbelt backend: a ``sandbox-exec`` SBPL profile.

The profile is allow-default with ``file-write*`` denied, then exactly the mode's
promised write effects allowed back. Two things follow from that shape, and both
are reported rather than left for a caller to infer:

- Reads are unrestricted, because the profile allows by default.
- ``ioctl`` is unrestricted, because ``file-write*`` is a different operation in
  the Seatbelt vocabulary and nothing denies ``ioctl``. On a device node that is
  openable for read — which ``allow default`` permits — an ioctl is how a serial
  or GPIO device is actually configured.

Both are scope notes rather than shortfalls: they lie outside what the
file-effect vocabulary covers, so they do not make the enforcement partial. They
do block a request that explicitly asks for network or process-visibility
confinement, which is what the shared satisfaction rule is for.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from odessy.execution.contract import ExecutionPolicy, Limitation, RunnerFailureRule
from odessy.execution.probe import HostFacts, discover_host_facts
from odessy.execution.profiles import seatbelt_command
from odessy.execution.providers.base import ConfiningProvider

__all__ = ["SeatbeltProvider"]


class SeatbeltProvider(ConfiningProvider):
    """Confines through a ``sandbox-exec`` profile."""

    provider_id = "seatbelt"

    #: The profile governs every file effect the modes promise. The entries
    #: returned by :meth:`limitations` are scope notes.
    GAP_LIMITATIONS = frozenset()

    DENIAL_SIGNATURES = ("operation not permitted",)

    RUNNER_FAILURE_RULES = (RunnerFailureRule(fatal_signatures=("sandbox-exec: ",)),)

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
        seatbelt = self._host().seatbelt
        return seatbelt.available, seatbelt.detail

    def limitations(self, policy: ExecutionPolicy) -> frozenset[Limitation]:
        # Derived from the profile's shape: what it denies, and what it therefore
        # leaves alone. Not verified against a live macOS host.
        return frozenset(
            {
                "cannot_restrict_reads",
                "cannot_restrict_network",
                "cannot_restrict_process_visibility",
                "cannot_restrict_ioctl",
                "host_kernel_shared",
            }
        )

    def wrap(self, argv: Sequence[str], policy: ExecutionPolicy) -> Sequence[str]:
        executable = self._executable or self._host().seatbelt.path
        if executable is None:  # pragma: no cover - usable() gates this
            raise ValueError("seatbelt is usable but has no executable path")
        return seatbelt_command(executable, argv, policy)

    def probe_facts(self) -> Mapping[str, object]:
        seatbelt = self._host().seatbelt
        return {"path": seatbelt.path, "available": seatbelt.available}

    def describe(self, policy: ExecutionPolicy) -> str:
        return "sandbox-exec with file-write* denied and the mode's writable roots allowed"
