"""Execution provider layer for Odessy.

Same-world process confinement, capability-driven provider selection, and the
managed-device surface. See ``docs/execution/PORT-MANIFEST.md`` for the upstream
lineage of each module and ``docs/execution/PROVIDER-PLACEHOLDERS.md`` for
capabilities that are declared but not yet backed by hardware.
"""

from odessy.execution.contract import (
    CAPABILITIES,
    CONFINED_MODES,
    DEVICE_CAPABILITIES,
    ENFORCEMENT_LEVELS,
    ESCALATION_OUTCOMES,
    ESCALATION_SUBJECTS,
    ESCALATION_TARGETS,
    ISOLATION_REQUIREMENTS,
    LIMITATIONS,
    MODES,
    WIDER_MODES,
    Capability,
    CapabilityProvider,
    ConfineResult,
    ConfinedMode,
    DeviceCapability,
    Enforcement,
    Evaluation,
    ExecutionPolicy,
    ExecutionProvider,
    IsolationRequirement,
    Limitation,
    ManagedDeviceProvider,
    Mode,
    ProbeReport,
    ProviderContractError,
    ProviderUnavailableError,
    Requirement,
    RunnerFailureRule,
    UnsatisfiableRequirementError,
    escalation_hint,
    sandbox_denial_marker,
    validate_confine_result,
    validate_evaluation,
)
from odessy.execution import (
    diagnostics,
    escalation,
    probe,
    profiles,
    providers,
    registry,
    roots,
)

__all__ = [
    "CAPABILITIES",
    "CONFINED_MODES",
    "DEVICE_CAPABILITIES",
    "ENFORCEMENT_LEVELS",
    "ESCALATION_OUTCOMES",
    "ESCALATION_SUBJECTS",
    "ESCALATION_TARGETS",
    "ISOLATION_REQUIREMENTS",
    "LIMITATIONS",
    "MODES",
    "WIDER_MODES",
    "Capability",
    "CapabilityProvider",
    "ConfineResult",
    "ConfinedMode",
    "DeviceCapability",
    "Enforcement",
    "Evaluation",
    "ExecutionPolicy",
    "ExecutionProvider",
    "IsolationRequirement",
    "Limitation",
    "ManagedDeviceProvider",
    "Mode",
    "ProbeReport",
    "ProviderContractError",
    "ProviderUnavailableError",
    "Requirement",
    "RunnerFailureRule",
    "UnsatisfiableRequirementError",
    "diagnostics",
    "escalation",
    "escalation_hint",
    "probe",
    "profiles",
    "providers",
    "registry",
    "roots",
    "sandbox_denial_marker",
    "validate_confine_result",
    "validate_evaluation",
]

# Convenience re-exports for the selection surface.
from odessy.execution.probe import HostFacts, discover_host_facts, render_host_facts
from odessy.execution.providers import (
    BwrapProvider,
    ConfiningProvider,
    LandlockProvider,
    SeatbeltProvider,
    UnconfinedProvider,
    default_execution_providers,
)
from odessy.execution.registry import ProviderRegistry, Selection, render_selection

__all__ += [
    "BwrapProvider",
    "ConfiningProvider",
    "HostFacts",
    "LandlockProvider",
    "ProviderRegistry",
    "SeatbeltProvider",
    "Selection",
    "UnconfinedProvider",
    "default_execution_providers",
    "discover_host_facts",
    "render_host_facts",
    "render_selection",
]
