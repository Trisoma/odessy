"""Execution providers.

Each provider answers one question: *can this host confine under this request,
and how completely?* Providers are selected by capability, never by platform
name. See :mod:`odessy.execution.registry`.
"""

from odessy.execution.providers.base import ConfiningProvider
from odessy.execution.providers.bwrap import BwrapProvider
from odessy.execution.providers.landlock import LandlockProvider
from odessy.execution.providers.seatbelt import SeatbeltProvider
from odessy.execution.providers.unconfined import UnconfinedProvider
from odessy.execution.providers.windows_acl import WindowsAclProvider

__all__ = [
    "BwrapProvider",
    "ConfiningProvider",
    "LandlockProvider",
    "SeatbeltProvider",
    "UnconfinedProvider",
    "WindowsAclProvider",
    "default_execution_providers",
]


def default_execution_providers() -> list[ConfiningProvider]:
    """The same-world backends Odessy ships, in preference order.

    The order is a tie-break only: it decides between providers that already
    satisfy the request, and can never turn an unsatisfied request into a
    satisfied one. bwrap leads because it restricts more by construction — a
    private PID namespace and a fresh ``/dev`` — than the other two, which
    restrict file writes and little else.

    On any given host most of these report themselves unavailable, which is the
    intended outcome: an unavailable backend is a fact the report states, not a
    failure.
    """

    return [BwrapProvider(), LandlockProvider(), SeatbeltProvider(), WindowsAclProvider()]
