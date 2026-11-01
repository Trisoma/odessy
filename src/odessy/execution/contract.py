"""The shared execution-provider contract.

This module defines the vocabulary, the data shapes, the provider protocols, and
the failure modes that both the Python and the TypeScript implementation of the
execution layer agree on. The vocabulary itself lives in ``contract.json`` next
to this file; this module holds the typed Python view of it.

Scope of ``contract.json``
--------------------------
The JSON is the canonical **cross-language vocabulary and protocol strings**
only. It is deliberately not the semantic definition of the layer: ABI-to-
limitation mapping, probe logic, selection policy, and per-provider guarantees
belong in code and in tests, not in a configuration file. If a change to this
module would require the JSON to describe behaviour rather than a name, the
change belongs somewhere else.

Invariants carried over from the DeepSeek Harness
------------------------------------------------
Each of these exists because the corresponding failure has already been paid for
once, upstream:

``confine`` takes exact argv, never a shell string.
    A shell string would move the question of argument splitting into the
    confinement layer, where it cannot be answered correctly.

Silent unconfined passthrough is forbidden.
    A provider that cannot enforce the requested mode raises. It never returns
    the caller's argv unchanged while reporting success.

Denial dialects are per backend, never a cross-backend union.
    A union claims denials a given backend never produces, so a broken runner
    would be misread as a policy denial.

Runner failure is not command denial.
    Runner failure means the command never ran. Denial means confinement worked
    and blocked it. Conflating them turns a broken sandbox into a confusing
    command error.

Policy rides the call.
    The policy is carried per call and is never fixed on the provider, because
    two consumers may confine under different policies at the same instant.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

# --------------------------------------------------------------------------
# Contract file
# --------------------------------------------------------------------------

_CONTRACT_PATH = Path(__file__).resolve().parent / "contract.json"


def _load_contract() -> dict[str, object]:
    with _CONTRACT_PATH.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise AssertionError(f"{_CONTRACT_PATH} must contain a JSON object")
    return loaded


CONTRACT: Mapping[str, object] = _load_contract()
CONTRACT_VERSION: int = int(CONTRACT["version"])  # type: ignore[arg-type]


def _markers() -> Mapping[str, str]:
    markers = CONTRACT["markers"]
    if not isinstance(markers, dict):
        raise AssertionError("contract markers must be a JSON object")
    return markers  # type: ignore[return-value]


_MARKERS = _markers()

# --------------------------------------------------------------------------
# Vocabulary
#
# These are declared in code so that they are statically typed and
# autocompletable. tests/test_execution_contract.py asserts that each one equals
# the corresponding entry in contract.json, so drift in either direction fails.
# --------------------------------------------------------------------------

Mode = Literal["read-only", "workspace-write", "danger-full-access"]
ConfinedMode = Literal["read-only", "workspace-write"]
Enforcement = Literal["full", "partial", "none"]
IsolationRequirement = Literal["required", "preferred", "not-required"]
NetworkPolicy = Literal["allow", "deny"]
EscalationOutcome = Literal["allowed-once", "rejected", "cancelled", "unavailable"]
EscalationSubject = Literal["command", "operation"]

Capability = Literal[
    "process-exec",
    "fs-read",
    "fs-write",
    "network",
    "process-visibility",
]

DeviceCapability = Literal[
    "gpio",
    "pwm",
    "adc",
    "serial",
    "spi",
    "i2c",
    "can",
    "usb",
    "camera",
]

Limitation = Literal[
    "cannot_deny_truncate",
    "cannot_restrict_rename_between_dirs",
    "cannot_restrict_reads",
    "cannot_restrict_network",
    "cannot_restrict_ioctl",
    "cannot_restrict_process_visibility",
    "cannot_restrict_device_access",
    "cannot_restrict_hardlink_alias",
    "reliance_on_everyone_ace",
    "no_console_isolation",
    "host_kernel_shared",
    "cannot_hide_paths",
]

MODES: tuple[Mode, ...] = ("read-only", "workspace-write", "danger-full-access")
CONFINED_MODES: tuple[ConfinedMode, ...] = ("read-only", "workspace-write")

#: The escalation ladder: a closed table of strictly wider modes. A request that
#: does not widen is rejected without prompting a human.
#:
#: Keys are the confined modes; the value type is the full ``Mode`` because a
#: lookup with an effective mode of ``danger-full-access`` must be able to miss
#: and yield the empty tuple rather than raise. Upstream declares this as
#: ``Record<string, ...>`` for the same reason.
WIDER_MODES: Mapping[str, tuple[Mode, ...]] = {
    "read-only": ("workspace-write", "danger-full-access"),
    "workspace-write": ("danger-full-access",),
}

ESCALATION_TARGETS: tuple[Mode, ...] = ("workspace-write", "danger-full-access")
ESCALATION_OUTCOMES: tuple[EscalationOutcome, ...] = (
    "allowed-once",
    "rejected",
    "cancelled",
    "unavailable",
)
ESCALATION_SUBJECTS: tuple[EscalationSubject, ...] = ("command", "operation")

ENFORCEMENT_LEVELS: tuple[Enforcement, ...] = ("full", "partial", "none")
ISOLATION_REQUIREMENTS: tuple[IsolationRequirement, ...] = (
    "required",
    "preferred",
    "not-required",
)

NETWORK_POLICIES: tuple[NetworkPolicy, ...] = ("allow", "deny")
CAPABILITIES: tuple[Capability, ...] = (
    "process-exec",
    "fs-read",
    "fs-write",
    "network",
    "process-visibility",
)

DEVICE_CAPABILITIES: tuple[DeviceCapability, ...] = (
    "gpio",
    "pwm",
    "adc",
    "serial",
    "spi",
    "i2c",
    "can",
    "usb",
    "camera",
)

LIMITATIONS: tuple[Limitation, ...] = (
    "cannot_deny_truncate",
    "cannot_restrict_rename_between_dirs",
    "cannot_restrict_reads",
    "cannot_restrict_network",
    "cannot_restrict_ioctl",
    "cannot_restrict_process_visibility",
    "cannot_restrict_device_access",
    "cannot_restrict_hardlink_alias",
    "reliance_on_everyone_ace",
    "no_console_isolation",
    "host_kernel_shared",
    "cannot_hide_paths",
)

# --------------------------------------------------------------------------
# Failure modes
# --------------------------------------------------------------------------

#: Structured error code carried through the MCP error channel so a caller can
#: distinguish "no confinement available" from "the command failed".
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"

#: Structured error code for "isolation was required and nothing satisfied it".
REQUIREMENT_UNSATISFIED = "REQUIREMENT_UNSATISFIED"


class ProviderError(RuntimeError):
    """Base class for execution-layer failures."""

    code: str = "PROVIDER_ERROR"


class ProviderUnavailableError(ProviderError):
    """No usable backend exists for the requested mode.

    Raised rather than running the command unconfined. This is the fail-closed
    behaviour: refusing to run is always preferable to running while claiming a
    boundary that does not exist.
    """

    code = PROVIDER_UNAVAILABLE

    def __init__(self, mode: Mode, detail: str | None = None) -> None:
        message = (
            f'no confinement backend is usable for mode "{mode}" on this host; '
            "refusing to run the command unconfined"
        )
        if detail:
            message = f"{message}. Runner failure: {detail}"
        super().__init__(message)
        self.mode = mode
        self.detail = detail


class UnsatisfiableRequirementError(ProviderError):
    """Isolation was required, and no provider could supply it.

    Carries every candidate's evaluation so the caller can explain what was
    discovered and why it was insufficient. The selector raises this instead of
    choosing a weaker provider: a requirement that can be silently downgraded is
    not a requirement.
    """

    code = REQUIREMENT_UNSATISFIED

    def __init__(
        self,
        requirement: Requirement,
        evaluations: Sequence[tuple[str, Evaluation]],
    ) -> None:
        detail = "; ".join(
            f"{provider_id}: {evaluation.reason or evaluation.enforcement}"
            for provider_id, evaluation in evaluations
        )
        super().__init__(
            "isolation was required but no available provider satisfies the "
            f"requested policy (mode={requirement.policy.mode!r}, "
            f"capabilities={sorted(requirement.capabilities)}). {detail or 'no providers registered'}"
        )
        self.requirement = requirement
        self.evaluations = tuple(evaluations)


class ProviderContractError(ProviderError):
    """A provider violated the contract.

    This is an implementation defect, not a policy outcome.
    """

    code = "PROVIDER_CONTRACT_VIOLATION"


# --------------------------------------------------------------------------
# Data shapes
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """The file-effect policy one confined execution runs under.

    ``device_grants`` names device nodes the confined process may reach. It is
    part of the policy rather than only of the authority layer because a
    confined process that can open a device node can drive the attached hardware
    without any tool call, and therefore without the authority layer ever being
    consulted.
    """

    mode: Mode
    workspace_root: str
    device_grants: frozenset[str] = field(default_factory=frozenset)
    #: ``deny`` asks the backend to cut the network. Best effort: a backend that
    #: cannot keeps reporting ``cannot_restrict_network``, so the result says
    #: whether it held.
    network: NetworkPolicy = "allow"
    #: Absolute paths the confined process must not see (credential folders and
    #: the like), already expanded and checked by the executor. A backend that
    #: cannot hide them reports ``cannot_hide_paths``.
    hidden_paths: tuple[str, ...] = ()
    #: Absolute folders added to ``workspace-write``'s writable roots (package
    #: caches). Ignored under ``read-only``.
    extra_writable: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Requirement:
    """What a consumer is asking for, including how strict it is about it."""

    policy: ExecutionPolicy
    isolation: IsolationRequirement = "preferred"
    capabilities: frozenset[Capability] = field(
        default_factory=lambda: frozenset({"process-exec"})
    )


@dataclass(frozen=True, slots=True)
class RunnerFailureRule:
    """Evidence that the confinement runner failed before executing the command.

    A consumer applies ``allowed_exit_codes`` when present, removes
    ``informational_lines`` by case-insensitive exact line equality, then matches
    ``fatal_signatures`` case-insensitively within each remaining stderr line.
    Exit status alone never proves runner failure.
    """

    fatal_signatures: tuple[str, ...]
    allowed_exit_codes: tuple[int, ...] | None = None
    informational_lines: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfineResult:
    """The wrapped argv a caller should spawn, plus what that wrapping achieves.

    ``enforcement`` is the *actual* completeness the selected backend achieves,
    which may be weaker than what :meth:`CapabilityProvider.evaluate` predicted.
    A weaker actual result must be surfaced, never swallowed.
    """

    argv: tuple[str, ...]
    enforcement: Enforcement
    limitations: frozenset[Limitation]
    denial_signatures: tuple[str, ...]
    runner_failure_rules: tuple[RunnerFailureRule, ...]
    provider_id: str


@dataclass(frozen=True, slots=True)
class Evaluation:
    """One provider's answer to one requirement.

    ``satisfied`` is the answer; the rest is the explanation. ``limitations``
    describes the *shortfall* under ``partial``, and the *scope* of the
    mechanism under ``full`` (for example ``host_kernel_shared``, which is a
    property of every same-world backend and not a defect).
    """

    satisfied: bool
    enforcement: Enforcement
    limitations: frozenset[Limitation]
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ProbeReport:
    """What a probe discovered about this host for one provider.

    Probe reports are always surfaced to the user: capability-driven selection
    can otherwise surprise, and an unexplained selection is indistinguishable
    from a wrong one.
    """

    provider_id: str
    available: bool
    facts: Mapping[str, object] = field(default_factory=dict)
    reason: str = ""


# --------------------------------------------------------------------------
# Contract enforcement
# --------------------------------------------------------------------------


def validate_evaluation(evaluation: Evaluation, *, provider_id: str) -> None:
    """Assert the ``partial`` invariant on an evaluation.

    ``partial`` must describe a known limitation. A provider that reports
    ``partial`` without naming what is partial is telling the caller that
    something is missing without saying what, which is indistinguishable from
    "the mechanism merely exists".
    """

    if evaluation.enforcement == "partial" and not evaluation.limitations:
        raise ProviderContractError(
            f'provider "{provider_id}" reported enforcement "partial" with no '
            "declared limitation; partial must describe a known limitation"
        )


def validate_confine_result(result: ConfineResult) -> None:
    """Assert the ``partial`` invariant on an actual confinement result."""

    if result.enforcement == "partial" and not result.limitations:
        raise ProviderContractError(
            f'provider "{result.provider_id}" reported enforcement "partial" with '
            "no declared limitation; partial must describe a known limitation"
        )


# --------------------------------------------------------------------------
# Satisfaction
# --------------------------------------------------------------------------

#: Which capability a scope limitation defeats. A limitation only blocks a
#: request that actually asks for the dimension it concerns: a provider that
#: cannot restrict network is perfectly adequate for a request that never claimed
#: to need network confinement.
_CAPABILITY_LIMITATIONS: Mapping[Capability, Limitation] = {
    "network": "cannot_restrict_network",
    "process-visibility": "cannot_restrict_process_visibility",
}


def blocking_limitations(
    requirement: Requirement,
    limitations: frozenset[Limitation],
) -> tuple[Limitation, ...]:
    """The subset of ``limitations`` that defeats *this* request.

    This is what keeps ``partial`` meaningful. A backend that reports a
    limitation the request never asked about still satisfies the request; a
    backend that reports the limitation the request *did* ask about does not.

    Device grants are deliberately **not** consulted here yet. Whether an
    inability to restrict device access defeats a request depends on the
    device-grant semantics, which are defined with the device layer. Inventing
    them now would be guessing. Until then, device limitations are reported as
    scope notes and block nothing.
    """

    return tuple(
        sorted(
            limitation
            for capability, limitation in _CAPABILITY_LIMITATIONS.items()
            if capability in requirement.capabilities and limitation in limitations
        )
    )


def required_isolation_is_met(
    requirement: Requirement,
    enforcement: Enforcement,
) -> bool:
    """Whether ``enforcement`` honours a *required* isolation request.

    ``partial`` means the backend governs only a subset of the effects the mode
    promises. That is an acceptable answer to a request that merely prefers
    isolation, and an unacceptable one to a request that requires it. This is the
    rule that makes ``required`` mean an absolute boundary rather than a
    preference for one.
    """

    if requirement.isolation != "required":
        return True
    return enforcement == "full"


# --------------------------------------------------------------------------
# Provider protocols
# --------------------------------------------------------------------------


@runtime_checkable
class CapabilityProvider(Protocol):
    """Base protocol: what exists here, and whether it satisfies a request.

    There is deliberately no static ``guarantees()`` accessor. A guarantee is
    only meaningful relative to a request, so it is produced by
    :meth:`evaluate` and, for an actual run, by ``confine``. Declaring guarantees
    independently would force every provider to enumerate an unbounded guarantee
    surface and would still fail to express "full for this request, partial for
    that one".

    Cost discipline: :meth:`probe` is the only member that touches the host, and
    its result is cached per process. :meth:`evaluate` must be a pure function of
    cached facts and the requirement, and must never re-run a probe.
    """

    provider_id: str

    def capabilities(self) -> frozenset[Capability]:
        """Coarse static declaration used to pre-filter candidates."""
        ...

    def probe(self) -> ProbeReport:
        """Discover whether this provider is usable on this host."""
        ...

    def evaluate(self, requirement: Requirement) -> Evaluation:
        """Decide whether this provider satisfies ``requirement``, and why."""
        ...


@runtime_checkable
class ExecutionProvider(CapabilityProvider, Protocol):
    """A same-world or different-world provider that runs confined work.

    ``confine`` takes exact argv rather than a shell string and returns the argv
    to spawn in its place. It does not spawn: process lifecycle stays with the
    executor, which keeps the provider stateless and directly testable.

    dsh's ``confine`` accepts an ``AbortSignal``. Odessy omits it because no
    Odessy caller currently cancels a confine call; termination is timeout-driven
    and owned by the executor. Add it when a caller needs it.
    """

    def confine(
        self,
        argv: Sequence[str],
        policy: ExecutionPolicy,
    ) -> ConfineResult:
        """Wrap ``argv`` for confinement, or raise rather than pass through."""
        ...


@runtime_checkable
class ManagedDeviceProvider(CapabilityProvider, Protocol):
    """A device Odessy manages across a link, not an execution environment.

    There is deliberately no ``confine`` and no ``execute``. An MCU has no
    processes and Odessy never runs code on one: the host reasons and orchestrates,
    and the device performs low-level real-time work. Device access is a
    permission surface under the authority layer, and the ability of confined
    process code to reach the device directly is governed by the isolation layer.

    The read/write/transaction surface is defined with the device layer.
    """

    def device_capabilities(self) -> frozenset[DeviceCapability]:
        """Which device-level capabilities this provider offers."""
        ...


# --------------------------------------------------------------------------
# Protocol strings
# --------------------------------------------------------------------------


def sandbox_denial_marker(mode: Mode) -> str:
    """The marker a backend emits for a file effect it denied under ``mode``."""

    return _MARKERS["denial"].format(mode=mode)


def escalation_hint(subject: EscalationSubject) -> str:
    """The marker telling the model an escalation retry is available once."""

    return _MARKERS["escalation_hint"].format(subject=subject)
