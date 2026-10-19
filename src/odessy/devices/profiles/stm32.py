"""STM32 — declared, not backed.

The capability list is what the family is *expected* to offer. It has not been
verified against a datasheet or exercised against a device.
"""

from __future__ import annotations

from odessy.devices.base import DeviceDescriptor

DESCRIPTOR = DeviceDescriptor(
    family="stm32",
    intended_capabilities=frozenset(
        {"gpio", "pwm", "adc", "serial", "spi", "i2c", "can", "usb"}
    ),
    status="declared",
    unbacked_reason=(
        "no transport implementation, no device-conformance harness, and no firmware "
        "contract exist; the intended capability list is unverified"
    ),
)
