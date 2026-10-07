"""The explicitly declared absence of confinement.

This provider is not a confinement backend. It is the *named, selectable,
reportable* statement that no backend is in use, and it exists for one structural
reason: selection must be total. A caller that can receive "no provider" is a
caller that can forget to handle it. A caller that receives this provider has
been told, in the contract's own vocabulary, that `enforcement` is `none`.

Why this is not "silent unconfined passthrough"
----------------------------------------------
The forbidden shape is a provider that returns the caller's argv unchanged while
reporting success, so the caller believes a boundary exists. This provider does
the opposite: it returns the argv unchanged and reports ``enforcement="none"``
with a reason. The absence is loud.

The registry is what keeps that honest. It refuses to select this provider when
``isolation == "required"``, so the only way to reach unconfined execution is to
have asked for something weaker than a guarantee — or to have registered a
registry that contains nothing else.
"""

from __future__ import annotations

from collections.abc import Sequence

from odessy.execution.contract import (
    CAPABILITIES,
    Capability,
    ConfineResult,
    Enforcement,
    Evaluation,
    ExecutionPolicy,
    ProbeReport,
    Requirement,
)

__all__ = ["UnconfinedProvider"]

_PROVIDER_ID = "unconfined"


class UnconfinedProvider:
    """Runs the caller's argv with no confinement, and says so."""

    provider_id = _PROVIDER_ID

    def capabilities(self) -> frozenset[Capability]:
        """Claim everything.

        This provider governs nothing, but it must survive the registry's
        capability pre-filter, otherwise a request needing any capability would
        filter it out and selection would have no unconfined option to offer.
        """

        return frozenset(CAPABILITIES)

    def probe(self) -> ProbeReport:
        return ProbeReport(
            provider_id=_PROVIDER_ID,
            available=True,
            facts={"confinement": "none"},
            reason="no confinement backend; the absence is reported rather than concealed",
        )

    def evaluate(self, requirement: Requirement) -> Evaluation:
        if requirement.isolation == "required":
            return Evaluation(
                satisfied=False,
                enforcement="none",
                limitations=frozenset(),
                reason="isolation is required and this host supplies no confinement backend",
            )
        return Evaluation(
            satisfied=True,
            enforcement="none",
            limitations=frozenset(),
            reason=(
                "running unconfined because isolation is not required "
                f"(requested {requirement.isolation})"
            ),
        )

    def confine(self, argv: Sequence[str], policy: ExecutionPolicy) -> ConfineResult:
        """Return the argv unchanged, and report that nothing was added.

        The requested mode is deliberately *not* checked. Refusing here would
        make an unconfined fallback impossible to express, which is the thing
        this provider exists to express. The requested mode is instead reported
        as unmet by ``enforcement="none"``, and the registry decides whether that
        is acceptable for the request.
        """

        return ConfineResult(
            argv=tuple(argv),
            enforcement="none",
            limitations=frozenset(),
            denial_signatures=(),
            runner_failure_rules=(),
            provider_id=_PROVIDER_ID,
        )


#: The enforcement levels this provider can ever report. Typed for the registry.
UNCONFINED_ENFORCEMENT: Enforcement = "none"
