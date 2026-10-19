"""The managed-device layer.

An MCU is a **managed device, not an execution environment**: it has no processes, and
Odessy never runs code on one. Device access is a permission surface under the
authority layer, and the ability of confined process code to reach the device directly
is governed by the isolation layer.

## The state of this layer

Three families are **declared** and none is **backed**. That distinction is kept
structurally rather than by convention, and the two functions below are how:

- :func:`declared_device_families` returns *descriptors* — plain data about what each
  family is intended to be, and what stands between it and support.
- :func:`default_device_providers` returns the *providers* Odessy ships, and returns
  nothing at all.

Because registration is fed from the second function and it is empty, there is no code
path by which a declaration can reach a caller as a selection. A placeholder cannot be
returned to anyone, not because a flag says so, but because there is nothing to return.

See ``docs/execution/PROVIDER-PLACEHOLDERS.md`` for the full list of what is declared
but unbacked, across both this layer and the execution providers.
"""

from __future__ import annotations

from odessy.devices.base import (
    DEVICE_FAMILIES,
    DEVICE_STATUSES,
    DeviceDescriptor,
    DeviceFamily,
    DeviceStatus,
)
from odessy.devices.profiles.esp32 import DESCRIPTOR as ESP32_DESCRIPTOR
from odessy.devices.profiles.rp2040 import DESCRIPTOR as RP2040_DESCRIPTOR
from odessy.devices.profiles.stm32 import DESCRIPTOR as STM32_DESCRIPTOR
from odessy.execution.contract import ManagedDeviceProvider

__all__ = [
    "DEVICE_FAMILIES",
    "DEVICE_STATUSES",
    "DeviceDescriptor",
    "DeviceFamily",
    "DeviceStatus",
    "declared_device_families",
    "default_device_providers",
]

_DECLARED: tuple[DeviceDescriptor, ...] = (
    ESP32_DESCRIPTOR,
    STM32_DESCRIPTOR,
    RP2040_DESCRIPTOR,
)


def declared_device_families() -> tuple[DeviceDescriptor, ...]:
    """Every device family Odessy declares, with what stands between it and support.

    Declarations are reported rather than hidden for the same reason probe reports are:
    a capability-driven system that silently lacks something is indistinguishable from
    one that silently has it.
    """

    return _DECLARED


def default_device_providers() -> tuple[ManagedDeviceProvider, ...]:
    """The device providers Odessy ships. Deliberately **empty**.

    No device backend exists, so returning nothing is the entire answer. It is also the
    mechanism that keeps :func:`declared_device_families` from becoming a lie: nothing
    registers from anywhere else, so no placeholder is reachable as a provider.

    Returning an empty tuple rather than raising is deliberate. A caller asking what
    device support exists should receive the honest answer — none — and be able to
    proceed without special-casing an exception.
    """

    return ()
