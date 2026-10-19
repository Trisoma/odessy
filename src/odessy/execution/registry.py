"""Provider registration and capability-driven selection.

Selection answers one question: *given what this host actually offers, which
provider satisfies this request?* It is a filter, not a ranking. A provider either
satisfies the request or it does not, and when the request requires isolation and
nothing satisfies it, selection **denies**. It never reaches for a weaker provider
to keep the command running, because a requirement that can be silently
downgraded is not a requirement.

```text
discover -> describe -> check satisfaction -> select or deny
```

Caching note, and a deliberate difference from upstream: upstream probes once per
provider lifetime and caches the verdict. Odessy caches the *host facts* instead
(:func:`odessy.execution.probe.discover_host_facts`), which is both shared across
providers and finer grained. That is what lets ``evaluate`` stay a pure function
of cached facts and the request rather than re-probing per candidate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from odessy.execution.contract import (
    Enforcement,
    Evaluation,
    ExecutionProvider,
    ProbeReport,
    Requirement,
    UnsatisfiableRequirementError,
    validate_evaluation,
)
from odessy.execution.providers import UnconfinedProvider

__all__ = ["ProviderRegistry", "Selection", "render_selection", "with_unconfined_fallback"]

#: Strongest first. Used only to choose *among* providers that already satisfy the
#: request, so it can never turn an unsatisfied request into a satisfied one.
_ENFORCEMENT_RANK: Mapping[Enforcement, int] = {"full": 2, "partial": 1, "none": 0}


def _unsatisfied(reason: str) -> Evaluation:
    return Evaluation(
        satisfied=False, enforcement="none", limitations=frozenset(), reason=reason
    )


@dataclass(frozen=True, slots=True)
class Selection:
    """The chosen provider, plus everything that was considered.

    The full picture is carried because capability-driven selection has to be
    explainable: an unexplained selection is indistinguishable from a wrong one.
    """

    requirement: Requirement
    provider: ExecutionProvider
    evaluation: Evaluation
    #: Every provider that was considered, and its answer, in registration order.
    considered: tuple[tuple[str, Evaluation], ...]
    #: Providers that failed their own probe, with the evidence.
    unavailable: tuple[ProbeReport, ...]


class ProviderRegistry:
    """An ordered set of execution providers."""

    def __init__(self, providers: Sequence[ExecutionProvider] = ()) -> None:
        self._providers: list[ExecutionProvider] = list(providers)

    def register(self, provider: ExecutionProvider) -> None:
        self._providers.append(provider)

    def providers(self) -> tuple[ExecutionProvider, ...]:
        return tuple(self._providers)

    def select(self, requirement: Requirement) -> Selection:
        """Choose the provider that satisfies ``requirement``.

        Raises :class:`~odessy.execution.contract.UnsatisfiableRequirementError`
        when nothing satisfies it, whatever the isolation level. Registering an
        :class:`~odessy.execution.providers.UnconfinedProvider` is how a caller
        says "and running unconfined is an acceptable outcome here"; without one,
        a request that nothing satisfies has no answer.
        """

        considered: list[tuple[str, Evaluation]] = []
        unavailable: list[ProbeReport] = []
        satisfied: list[tuple[ExecutionProvider, Evaluation]] = []

        for provider in self._providers:
            missing = requirement.capabilities - provider.capabilities()
            if missing:
                considered.append(
                    (
                        provider.provider_id,
                        _unsatisfied(f"does not offer capabilities: {sorted(missing)}"),
                    )
                )
                continue

            report = provider.probe()
            if not report.available:
                unavailable.append(report)
                considered.append(
                    (provider.provider_id, _unsatisfied(f"unavailable: {report.reason}"))
                )
                continue

            evaluation = provider.evaluate(requirement)
            # A provider that reports `partial` without naming a limitation is
            # defective, not merely unsatisfying. That must surface here rather
            # than be recorded as a normal outcome.
            validate_evaluation(evaluation, provider_id=provider.provider_id)

            considered.append((provider.provider_id, evaluation))
            if evaluation.satisfied:
                satisfied.append((provider, evaluation))

        if not satisfied:
            raise UnsatisfiableRequirementError(requirement, tuple(considered))

        # Ties resolve to the earliest registration, because `max` returns the
        # first maximal element.
        provider, evaluation = max(
            satisfied, key=lambda pair: _ENFORCEMENT_RANK[pair[1].enforcement]
        )

        return Selection(
            requirement=requirement,
            provider=provider,
            evaluation=evaluation,
            considered=tuple(considered),
            unavailable=tuple(unavailable),
        )


def with_unconfined_fallback(
    providers: Sequence[ExecutionProvider],
) -> ProviderRegistry:
    """Build a registry whose last resort is explicitly unconfined execution.

    The fallback is appended last so that, at equal enforcement rank, a real
    confinement backend always wins the tie. It can still only be selected when
    the request does not require isolation.
    """

    registry = ProviderRegistry(providers)
    registry.register(UnconfinedProvider())
    return registry


def render_selection(selection: Selection) -> str:
    """Render a selection decision for a human."""

    lines = [
        f"selected {selection.provider.provider_id}"
        f" (enforcement={selection.evaluation.enforcement})"
    ]
    if selection.evaluation.reason:
        lines.append(f"  reason: {selection.evaluation.reason}")
    if selection.evaluation.limitations:
        lines.append(f"  limitations: {', '.join(sorted(selection.evaluation.limitations))}")

    lines.append("  considered:")
    for provider_id, evaluation in selection.considered:
        mark = "satisfied" if evaluation.satisfied else "no"
        lines.append(
            f"    {mark:<9} {provider_id}: {evaluation.reason or evaluation.enforcement}"
        )

    for report in selection.unavailable:
        lines.append(f"    unavailable {report.provider_id}: {report.reason}")

    return "\n".join(lines)
