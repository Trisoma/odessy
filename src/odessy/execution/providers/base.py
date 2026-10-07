"""Shared behaviour for providers that confine by wrapping argv.

The backend-specific parts are the facts, the limitation set, and the argv
transform. The parts that must never be skipped — the fail-closed check on
:meth:`ConfiningProvider.confine` and the satisfaction rule on
:meth:`ConfiningProvider.evaluate` — live here, in one place, so that adding a
backend cannot quietly omit them.

The two-way split of reported limitations is the mechanism that keeps ``partial``
meaningful:

``GAP_LIMITATIONS``
    A shortfall in the effects the mode *promises*. Its presence makes the
    enforcement ``partial``, because the backend governs only a subset of what it
    claims to.

*everything else*
    A scope note. True of the mechanism, outside what the file-effect vocabulary
    covers, and not a defect: a same-world backend sharing the host kernel is not
    failing to do something it promised.

A backend is free to report scope notes under ``full`` enforcement. It is not
free to report ``partial`` without naming what is partial — the contract test
enforces that.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar

from odessy.execution.contract import (
    CONFINED_MODES,
    Capability,
    ConfineResult,
    Enforcement,
    Evaluation,
    ExecutionPolicy,
    Limitation,
    ProbeReport,
    ProviderUnavailableError,
    Requirement,
    RunnerFailureRule,
    blocking_limitations,
    required_isolation_is_met,
)

__all__ = ["ConfiningProvider"]

#: What every argv-wrapping backend offers. None of them can be *asked* to govern
#: network or process visibility, because their right vocabularies do not extend
#: there; whether they restrict those anyway is reported through limitations.
_CONFINEMENT_CAPABILITIES: frozenset[Capability] = frozenset(
    {"process-exec", "fs-read", "fs-write"}
)


class ConfiningProvider:
    """A provider that confines by returning a wrapped argv."""

    provider_id: str = ""

    GAP_LIMITATIONS: ClassVar[frozenset[Limitation]] = frozenset()

    #: The selected backend's denial dialect — never a cross-backend union.
    DENIAL_SIGNATURES: ClassVar[tuple[str, ...]] = ()

    #: Structured evidence that the runner failed before running the command.
    RUNNER_FAILURE_RULES: ClassVar[tuple[RunnerFailureRule, ...]] = ()

    # ----------------------------------------------------------------------
    # Subclass hooks
    # ----------------------------------------------------------------------

    def usable(self) -> tuple[bool, str]:
        """``(usable, reason)`` for this host.

        Must read cached host facts rather than probing, so that ``evaluate``
        stays a pure function and selection does not re-probe per candidate.
        """

        raise NotImplementedError

    def limitations(self, policy: ExecutionPolicy) -> frozenset[Limitation]:
        """The limitations that apply to *this* policy on *this* host."""

        raise NotImplementedError

    def wrap(self, argv: Sequence[str], policy: ExecutionPolicy) -> Sequence[str]:
        """The argv to spawn in place of ``argv``."""

        raise NotImplementedError

    def probe_facts(self) -> Mapping[str, object]:
        """Backend-specific evidence for the probe report."""

        return {}

    def describe(self, policy: ExecutionPolicy) -> str:
        """One line explaining how this backend confines under ``policy``."""

        return f"{self.provider_id} confines under {policy.mode}"

    # ----------------------------------------------------------------------
    # Shared behaviour
    # ----------------------------------------------------------------------

    def capabilities(self) -> frozenset[Capability]:
        return _CONFINEMENT_CAPABILITIES

    def enforcement(self, limitations: frozenset[Limitation]) -> Enforcement:
        """``partial`` exactly when a promised effect is not governed."""

        return "partial" if limitations & self.GAP_LIMITATIONS else "full"

    def probe(self) -> ProbeReport:
        usable, reason = self.usable()
        return ProbeReport(
            provider_id=self.provider_id,
            available=usable,
            facts=dict(self.probe_facts()),
            reason=reason,
        )

    def evaluate(self, requirement: Requirement) -> Evaluation:
        mode = requirement.policy.mode
        if mode not in CONFINED_MODES:
            return Evaluation(
                satisfied=False,
                enforcement="none",
                limitations=frozenset(),
                reason=f"{mode} requests no confinement; a confining provider cannot satisfy it",
            )

        usable, reason = self.usable()
        if not usable:
            return Evaluation(
                satisfied=False,
                enforcement="none",
                limitations=frozenset(),
                reason=reason,
            )

        limitations = self.limitations(requirement.policy)
        enforcement = self.enforcement(limitations)

        if not required_isolation_is_met(requirement, enforcement):
            gaps = ", ".join(sorted(limitations & self.GAP_LIMITATIONS))
            return Evaluation(
                satisfied=False,
                enforcement=enforcement,
                limitations=limitations,
                reason=(
                    "required isolation needs an absolute boundary, and this backend "
                    f"reports partial enforcement: {gaps}"
                ),
            )

        blocking = blocking_limitations(requirement, limitations)
        if blocking:
            return Evaluation(
                satisfied=False,
                enforcement=enforcement,
                limitations=limitations,
                reason=(
                    "the request needs a dimension this backend cannot restrict: "
                    f"{', '.join(blocking)}"
                ),
            )

        return Evaluation(
            satisfied=True,
            enforcement=enforcement,
            limitations=limitations,
            reason=self.describe(requirement.policy),
        )

    def confine(self, argv: Sequence[str], policy: ExecutionPolicy) -> ConfineResult:
        """Wrap ``argv``, or refuse.

        Refusing is the whole point of this method's placement. A backend that is
        unusable must raise rather than return the caller's argv, because a
        caller that receives an argv has no way to tell a wrapped one from an
        unwrapped one.
        """

        if policy.mode not in CONFINED_MODES:
            raise ValueError(
                f"{self.provider_id} was asked to confine under mode {policy.mode!r}, "
                "which requests no confinement"
            )

        usable, reason = self.usable()
        if not usable:
            raise ProviderUnavailableError(policy.mode, detail=reason)

        limitations = self.limitations(policy)
        return ConfineResult(
            argv=tuple(self.wrap(argv, policy)),
            enforcement=self.enforcement(limitations),
            limitations=limitations,
            denial_signatures=self.DENIAL_SIGNATURES,
            runner_failure_rules=self.RUNNER_FAILURE_RULES,
            provider_id=self.provider_id,
        )
