"""The device layer declares; it does not provide.

Phase 9 adds three device families to the record and **no device support at all**. These
tests exist to keep that honest, because the failure mode is not a crash — it is a
declaration quietly becoming usable, and then a caller believing a device is managed when
nothing is managing it.

The load-bearing assertion is :func:`test_no_declaration_can_be_a_provider`: everything
else describes the declarations, and that one describes the wall between them and the
rest of the system.
"""

from __future__ import annotations

import dataclasses

import pytest

from odessy.devices import (
    DEVICE_FAMILIES,
    DEVICE_STATUSES,
    declared_device_families,
    default_device_providers,
)
from odessy.devices.base import DeviceDescriptor
from odessy.execution.contract import (
    DEVICE_CAPABILITIES,
    ManagedDeviceProvider,
    Requirement,
)
from odessy.execution.registry import ProviderRegistry


# --------------------------------------------------------------------------
# The wall
# --------------------------------------------------------------------------


def test_no_device_provider_is_shipped() -> None:
    # The whole answer. There is no backend, so there is nothing to hand back, and a
    # caller asking what device support exists gets the honest answer without having to
    # special-case an exception.
    assert default_device_providers() == ()


def test_no_declaration_can_be_a_provider() -> None:
    # Declarations are data. Nothing here satisfies the provider Protocol, so a
    # descriptor cannot be registered or selected even by mistake -- the type is the
    # guarantee, not a flag someone has to remember to check.
    for descriptor in declared_device_families():
        assert not isinstance(descriptor, ManagedDeviceProvider), descriptor.family


def test_a_declaration_cannot_satisfy_an_execution_requirement() -> None:
    # The device layer is deliberately NOT part of the execution contract: an MCU has no
    # processes to confine. A registry populated from the device layer would therefore
    # have nothing to offer, which is what the empty provider tuple guarantees.
    registry = ProviderRegistry(default_device_providers())  # type: ignore[arg-type]

    with pytest.raises(Exception):
        registry.select(
            Requirement(
                policy={
                    "mode": "read-only",
                    "workspace_root": "/work",
                    "device_grants": frozenset(),
                }  # type: ignore[arg-type]
            )
        )


def test_every_declared_family_is_declared() -> None:
    # The status vocabulary has exactly one member. That is the honest encoding: the only
    # state any family is in today is "declared". A status that could already say
    # "available" would invite precisely the claim this layer exists to prevent.
    assert DEVICE_STATUSES == ("declared",)

    for descriptor in declared_device_families():
        assert descriptor.status == "declared", descriptor.family


# --------------------------------------------------------------------------
# The declarations themselves
# --------------------------------------------------------------------------


def test_declarations_cover_every_family_in_the_vocabulary() -> None:
    # A family in the vocabulary with no declaration is a silently missing statement, and
    # a capability-driven system that silently lacks something is indistinguishable from
    # one that silently has it.
    declared = {descriptor.family for descriptor in declared_device_families()}

    assert declared == set(DEVICE_FAMILIES)


def test_every_declaration_gives_a_reason_it_is_unbacked() -> None:
    # Required rather than optional by the type: a declaration without a reason is a claim
    # without evidence, and the reason is what lets a reader judge how far away the family
    # actually is.
    for descriptor in declared_device_families():
        assert descriptor.unbacked_reason.strip(), descriptor.family


def test_intended_capabilities_come_from_the_shared_vocabulary() -> None:
    # No invented capabilities. The device vocabulary lives in the execution contract, and
    # a family may only name things that contract already knows.
    for descriptor in declared_device_families():
        assert descriptor.intended_capabilities, descriptor.family
        unknown = descriptor.intended_capabilities - set(DEVICE_CAPABILITIES)
        assert not unknown, f"{descriptor.family}: {sorted(unknown)}"


def test_declarations_are_immutable() -> None:
    # A declaration that could be edited at runtime is not a declaration.
    assert dataclasses.is_dataclass(DeviceDescriptor)

    descriptor = declared_device_families()[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        descriptor.family = "esp32"  # type: ignore[misc]


def test_each_family_is_declared_exactly_once() -> None:
    families = [descriptor.family for descriptor in declared_device_families()]

    assert len(families) == len(set(families))
