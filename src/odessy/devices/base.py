"""The device layer's declaration vocabulary.

An MCU is a **managed device, not an execution environment**. There are no processes
on it and Odessy never runs code on one: the host reasons and orchestrates, and the
device performs low-level real-time work. Device access is therefore a *permission*
surface under the authority layer, and the ability of confined process code to reach
the device directly is governed by the isolation layer. Those are different questions
and neither substitutes for the other.

## Why this module contains no device API

There is no read, write, or transaction surface here, and that is deliberate rather
than unfinished. Designing an operation API with zero devices to drive it would be
guessing at what the operations are, and an abstraction with no demonstrated backend
is the thing this project has explicitly ruled out. The declarations below describe
*intent*; the operation surface gets defined when the first device is actually driven,
against something real.

What this module does provide is the vocabulary the authority layer needs in order to
name a device and a capability, so a grant can be expressed before a backend exists to
honour it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from odessy.execution.contract import DeviceCapability

__all__ = [
    "DEVICE_FAMILIES",
    "DEVICE_STATUSES",
    "DeviceDescriptor",
    "DeviceFamily",
    "DeviceStatus",
]

#: The device families Odessy intends to support.
#:
#: A closed vocabulary rather than free strings, so a policy cannot name a family
#: that no declaration covers, and so adding one is a visible change.
DeviceFamily = Literal["esp32", "stm32", "rp2040"]

DEVICE_FAMILIES: tuple[DeviceFamily, ...] = ("esp32", "stm32", "rp2040")

#: How far along a device family is.
#:
#: The vocabulary has exactly one member, and that is the honest encoding rather than
#: a placeholder list: the only state any family is in today is *declared*. When a
#: backend exists this grows, and the growth is the point at which the distinction
#: becomes meaningful. A status field that could already say "available" would invite
#: exactly the claim this module exists to prevent.
DeviceStatus = Literal["declared"]

DEVICE_STATUSES: tuple[DeviceStatus, ...] = ("declared",)


@dataclass(frozen=True, slots=True)
class DeviceDescriptor:
    """What a device family *is intended to be*, and why it is not that yet.

    A descriptor is **data, not a provider**. It exists so the project can state what
    it means to support without that statement being mistakable for support. Nothing
    in this module can turn a descriptor into something executable, and that is a
    property of the types rather than a convention: there is no code path from here to
    a `ManagedDeviceProvider`.
    """

    family: DeviceFamily
    #: What the family is expected to offer. Named *intended* because it has not been
    #: verified against a datasheet or a device, and a capability list presented as
    #: fact would be a claim with no evidence behind it.
    intended_capabilities: frozenset[DeviceCapability]
    status: DeviceStatus
    #: Why this family is not implemented, in one line.
    #:
    #: Required rather than optional: a declaration without a reason is a claim without
    #: evidence, and the reason is what a reader needs in order to judge how far away
    #: the family actually is.
    unbacked_reason: str
