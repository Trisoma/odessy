"""Contract parity and invariant tests for the execution provider layer.

Two jobs:

1. Assert that the typed Python vocabulary equals ``contract.json``, so drift in
   either direction fails here rather than in production.
2. Assert the contract's own invariants: the ``partial`` rule, the escalation
   ladder shape, the error codes, and the provider protocol boundaries.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from odessy.execution import contract as c

_CONTRACT_PATH = Path(c.__file__).resolve().parent / "contract.json"

#: Guard against ``contract.json`` growing into a specification file. It is the
#: cross-language vocabulary and protocol strings only; behaviour belongs in code
#: and tests. Adding a key here should be a deliberate act.
_EXPECTED_TOP_LEVEL_KEYS = {
    "$comment",
    "version",
    "modes",
    "confined_modes",
    "wider_modes",
    "escalation_targets",
    "escalation_outcomes",
    "escalation_subjects",
    "enforcement_levels",
    "isolation_requirements",
    "network_policies",
    "capabilities",
    "device_capabilities",
    "limitations",
    "markers",
}


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Vocabulary parity
# --------------------------------------------------------------------------


def test_contract_file_exists_and_is_scoped(raw: dict) -> None:
    assert _CONTRACT_PATH.is_file()
    assert set(raw) == _EXPECTED_TOP_LEVEL_KEYS, (
        "contract.json gained or lost a top-level key; it must stay a vocabulary "
        "and protocol-string file, not grow into a specification"
    )


def test_version_matches(raw: dict) -> None:
    assert raw["version"] == c.CONTRACT_VERSION


@pytest.mark.parametrize(
    ("json_key", "constant"),
    [
        ("modes", c.MODES),
        ("confined_modes", c.CONFINED_MODES),
        ("escalation_targets", c.ESCALATION_TARGETS),
        ("escalation_outcomes", c.ESCALATION_OUTCOMES),
        ("escalation_subjects", c.ESCALATION_SUBJECTS),
        ("enforcement_levels", c.ENFORCEMENT_LEVELS),
        ("isolation_requirements", c.ISOLATION_REQUIREMENTS),
        ("network_policies", c.NETWORK_POLICIES),
        ("capabilities", c.CAPABILITIES),
        ("device_capabilities", c.DEVICE_CAPABILITIES),
        ("limitations", c.LIMITATIONS),
    ],
)
def test_vocabulary_parity(raw: dict, json_key: str, constant: tuple[str, ...]) -> None:
    assert raw[json_key] == list(constant), f"{json_key} drifted between contract.json and Python"


def test_wider_modes_parity(raw: dict) -> None:
    expected = {mode: list(targets) for mode, targets in c.WIDER_MODES.items()}
    assert raw["wider_modes"] == expected


# --------------------------------------------------------------------------
# Vocabulary shape
# --------------------------------------------------------------------------


def test_confined_modes_exclude_full_access() -> None:
    assert set(c.CONFINED_MODES) == set(c.MODES) - {"danger-full-access"}


def test_escalation_ladder_is_strictly_wider_and_closed() -> None:
    assert "read-only" not in c.ESCALATION_TARGETS
    for source, targets in c.WIDER_MODES.items():
        assert source in c.CONFINED_MODES
        for target in targets:
            assert target in c.ESCALATION_TARGETS, f"{target} is not an escalation target"
            assert target != source, "an escalation target may not equal its source"
            assert c.MODES.index(target) > c.MODES.index(source), (
                f"{target} is not strictly wider than {source}"
            )


def test_every_limitation_is_a_known_name() -> None:
    assert "cannot_deny_truncate" in c.LIMITATIONS
    assert len(set(c.LIMITATIONS)) == len(c.LIMITATIONS), "limitation names must be unique"


# --------------------------------------------------------------------------
# Protocol strings
# --------------------------------------------------------------------------


def test_denial_marker_is_byte_exact() -> None:
    assert c.sandbox_denial_marker("read-only") == (
        "[sandbox: file access denied under read-only mode]"
    )
    assert c.sandbox_denial_marker("workspace-write") == (
        "[sandbox: file access denied under workspace-write mode]"
    )


def test_escalation_hint_is_byte_exact() -> None:
    assert c.escalation_hint("command") == (
        "[sandbox: escalation available — retry this exact command once with "
        "sandbox_permissions (the narrowest wider mode that suffices) + justification; "
        "the approval prompt asks the user]"
    )
    assert "this exact operation once" in c.escalation_hint("operation")


# --------------------------------------------------------------------------
# The partial invariant
# --------------------------------------------------------------------------


def test_partial_without_limitations_is_a_contract_violation() -> None:
    evaluation = c.Evaluation(
        satisfied=False, enforcement="partial", limitations=frozenset(), reason=""
    )
    with pytest.raises(c.ProviderContractError, match="no declared limitation"):
        c.validate_evaluation(evaluation, provider_id="demo")


def test_partial_with_a_named_limitation_is_valid() -> None:
    evaluation = c.Evaluation(
        satisfied=False,
        enforcement="partial",
        limitations=frozenset({"cannot_deny_truncate"}),
        reason="older ABI",
    )
    c.validate_evaluation(evaluation, provider_id="demo")


def test_full_may_carry_scope_limitations() -> None:
    """``host_kernel_shared`` is a property of same-world backends, not a defect."""

    evaluation = c.Evaluation(
        satisfied=True,
        enforcement="full",
        limitations=frozenset({"host_kernel_shared"}),
        reason="",
    )
    c.validate_evaluation(evaluation, provider_id="demo")


def test_none_needs_no_declared_limitation() -> None:
    evaluation = c.Evaluation(
        satisfied=False, enforcement="none", limitations=frozenset(), reason="no backend"
    )
    c.validate_evaluation(evaluation, provider_id="demo")


def test_confine_result_partial_invariant() -> None:
    result = c.ConfineResult(
        argv=("true",),
        enforcement="partial",
        limitations=frozenset(),
        denial_signatures=(),
        runner_failure_rules=(),
        provider_id="demo",
    )
    with pytest.raises(c.ProviderContractError, match="no declared limitation"):
        c.validate_confine_result(result)


# --------------------------------------------------------------------------
# Error codes
# --------------------------------------------------------------------------


def test_error_codes_are_distinguishable() -> None:
    assert c.ProviderUnavailableError("read-only").code == c.PROVIDER_UNAVAILABLE
    requirement = c.Requirement(policy=c.ExecutionPolicy(mode="read-only", workspace_root="/tmp"))
    assert c.UnsatisfiableRequirementError(requirement, ()).code == c.REQUIREMENT_UNSATISFIED
    assert c.ProviderContractError("x").code == "PROVIDER_CONTRACT_VIOLATION"


def test_provider_unavailable_says_it_will_not_run_unconfined() -> None:
    error = c.ProviderUnavailableError("workspace-write", detail="bwrap: missing")
    assert "refusing to run the command unconfined" in str(error)
    assert "bwrap: missing" in str(error)
    assert error.mode == "workspace-write"


def test_unsatisfiable_requirement_reports_every_candidate() -> None:
    requirement = c.Requirement(policy=c.ExecutionPolicy(mode="read-only", workspace_root="/tmp"))
    evaluations = (
        ("bwrap", c.Evaluation(True, "none", frozenset(), "bwrap binary is not installed")),
        ("landlock", c.Evaluation(True, "none", frozenset(), "kernel Landlock is not enabled")),
    )
    error = c.UnsatisfiableRequirementError(requirement, evaluations)
    assert "refusing" not in str(error)
    assert "bwrap binary is not installed" in str(error)
    assert "kernel Landlock is not enabled" in str(error)


# --------------------------------------------------------------------------
# Protocol boundaries
# --------------------------------------------------------------------------


class _DeviceOnlyProvider:
    """Satisfies ManagedDeviceProvider and must NOT satisfy ExecutionProvider."""

    provider_id = "esp32-link"

    def capabilities(self) -> frozenset[c.Capability]:
        return frozenset({"process-exec"})

    def probe(self) -> c.ProbeReport:
        return c.ProbeReport(provider_id=self.provider_id, available=False)

    def evaluate(self, requirement: c.Requirement) -> c.Evaluation:
        return c.Evaluation(True, "none", frozenset(), "not implemented")

    def device_capabilities(self) -> frozenset[c.DeviceCapability]:
        return frozenset({"serial"})


class _ExecutionOnlyProvider(_DeviceOnlyProvider):
    provider_id = "bwrap"

    def confine(self, argv, policy):  # type: ignore[no-untyped-def]
        return c.ConfineResult(
            argv=tuple(argv),
            enforcement="full",
            limitations=frozenset(),
            denial_signatures=(),
            runner_failure_rules=(),
            provider_id=self.provider_id,
        )


def test_a_managed_device_is_not_an_execution_provider() -> None:
    """An MCU has no processes; it must never be usable as an execution backend."""

    device = _DeviceOnlyProvider()
    assert isinstance(device, c.CapabilityProvider)
    assert isinstance(device, c.ManagedDeviceProvider)
    assert not isinstance(device, c.ExecutionProvider)


def test_an_execution_provider_satisfies_the_base_protocol() -> None:
    executor = _ExecutionOnlyProvider()
    assert isinstance(executor, c.CapabilityProvider)
    assert isinstance(executor, c.ExecutionProvider)


def test_managed_device_protocol_has_no_execution_members() -> None:
    for name in ("confine", "execute"):
        assert not hasattr(c.ManagedDeviceProvider, name), (
            f"ManagedDeviceProvider must not declare {name!r}: devices are managed, not executed"
        )
