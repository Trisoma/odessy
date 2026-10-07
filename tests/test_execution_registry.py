"""Provider selection.

The load-bearing test is the negative one: when isolation is required and the
host cannot satisfy it, selection must **deny** — not reach for a weaker
provider, and not let anything execute.
"""

from __future__ import annotations

import pytest

from odessy.execution.contract import (
    CAPABILITIES,
    Capability,
    ConfineResult,
    Evaluation,
    ExecutionPolicy,
    Mode,
    ProviderContractError,
    ProbeReport,
    Requirement,
    UnsatisfiableRequirementError,
)
from odessy.execution.providers import UnconfinedProvider
from odessy.execution.registry import (
    ProviderRegistry,
    render_selection,
    with_unconfined_fallback,
)


class _StubProvider:
    """A provider whose every answer is configured, and which records confine calls."""

    def __init__(
        self,
        provider_id: str,
        *,
        enforcement: str = "full",
        capabilities: frozenset[Capability] | None = None,
        available: bool = True,
        satisfied: bool = True,
        limitations: tuple[str, ...] = (),
        reason: str = "",
    ) -> None:
        self.provider_id = provider_id
        self._enforcement = enforcement
        self._capabilities = (
            frozenset(CAPABILITIES) if capabilities is None else capabilities
        )
        self._available = available
        self._satisfied = satisfied
        self._limitations = frozenset(limitations)
        self._reason = reason
        self.confine_calls: list[tuple[tuple[str, ...], ExecutionPolicy]] = []

    def capabilities(self) -> frozenset[Capability]:
        return self._capabilities

    def probe(self) -> ProbeReport:
        return ProbeReport(
            provider_id=self.provider_id,
            available=self._available,
            facts={},
            reason="" if self._available else "stub is unavailable",
        )

    def evaluate(self, requirement: Requirement) -> Evaluation:
        return Evaluation(
            satisfied=self._satisfied,
            enforcement=self._enforcement,  # type: ignore[arg-type]
            limitations=self._limitations,  # type: ignore[arg-type]
            reason=self._reason,
        )

    def confine(self, argv, policy):  # type: ignore[no-untyped-def]
        self.confine_calls.append((tuple(argv), policy))
        return ConfineResult(
            argv=tuple(argv),
            enforcement=self._enforcement,  # type: ignore[arg-type]
            limitations=self._limitations,  # type: ignore[arg-type]
            denial_signatures=(),
            runner_failure_rules=(),
            provider_id=self.provider_id,
        )


def _requirement(
    *,
    mode: Mode = "workspace-write",
    isolation: str = "preferred",
    capabilities: tuple[Capability, ...] = ("process-exec",),
) -> Requirement:
    return Requirement(
        policy=ExecutionPolicy(mode=mode, workspace_root="/tmp"),
        isolation=isolation,  # type: ignore[arg-type]
        capabilities=frozenset(capabilities),
    )


# --------------------------------------------------------------------------
# Selection among satisfying providers
# --------------------------------------------------------------------------


def test_the_satisfying_provider_is_selected() -> None:
    provider = _StubProvider("only")
    selection = ProviderRegistry([provider]).select(_requirement())

    assert selection.provider is provider
    assert selection.evaluation.enforcement == "full"


def test_the_strongest_enforcement_wins() -> None:
    """Ranking only chooses among providers that already satisfy the request."""

    partial = _StubProvider("partial", enforcement="partial", limitations=("cannot_deny_truncate",))
    full = _StubProvider("full", enforcement="full")
    unconfined = UnconfinedProvider()

    selection = ProviderRegistry([partial, full, unconfined]).select(_requirement())

    assert selection.provider.provider_id == "full"


def test_ties_resolve_to_the_earliest_registration() -> None:
    first = _StubProvider("first", enforcement="full")
    second = _StubProvider("second", enforcement="full")

    assert ProviderRegistry([first, second]).select(_requirement()).provider is first
    assert ProviderRegistry([second, first]).select(_requirement()).provider is second


def test_the_selection_explains_every_candidate() -> None:
    satisfied = _StubProvider("satisfied", reason="host supports it")
    rejected = _StubProvider("rejected", satisfied=False, reason="no backend")

    selection = ProviderRegistry([satisfied, rejected]).select(_requirement())

    assert [provider_id for provider_id, _ in selection.considered] == ["satisfied", "rejected"]


# --------------------------------------------------------------------------
# Pre-filtering and unavailability
# --------------------------------------------------------------------------


def test_a_provider_without_the_needed_capability_is_filtered_out() -> None:
    narrow = _StubProvider("narrow", capabilities=frozenset({"process-exec"}))
    wide = _StubProvider("wide", capabilities=frozenset({"process-exec", "network"}))

    selection = ProviderRegistry([narrow, wide]).select(
        _requirement(capabilities=("process-exec", "network"))
    )

    assert selection.provider.provider_id == "wide"
    reasons = dict(selection.considered)
    assert "does not offer capabilities" in reasons["narrow"].reason


def test_an_unavailable_provider_is_recorded_but_not_selected() -> None:
    broken = _StubProvider("broken", available=False)
    working = _StubProvider("working")

    selection = ProviderRegistry([broken, working]).select(_requirement())

    assert selection.provider.provider_id == "working"
    assert [report.provider_id for report in selection.unavailable] == ["broken"]
    assert "unavailable" in dict(selection.considered)["broken"].reason


def test_an_unavailable_provider_never_satisfies() -> None:
    """Availability is decided before evaluation, so a lying provider cannot win."""

    liar = _StubProvider("liar", available=False, satisfied=True)

    with pytest.raises(UnsatisfiableRequirementError):
        ProviderRegistry([liar]).select(_requirement())


# --------------------------------------------------------------------------
# The negative test: required means required
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider",
    [_StubProvider("broken", available=False), _StubProvider("unsatisfied", satisfied=False)],
)
def test_required_isolation_denies_when_nothing_satisfies_it(provider: _StubProvider) -> None:
    registry = ProviderRegistry([provider])

    with pytest.raises(UnsatisfiableRequirementError):
        registry.select(_requirement(isolation="required"))

    assert provider.confine_calls == [], "nothing may run after a denied selection"


def test_required_isolation_refuses_the_unconfined_fallback() -> None:
    """The property the whole requirement axis exists for.

    An unconfined provider is registered and available, but it reports
    ``satisfied=False`` when isolation is required, so selection denies rather
    than downgrading the guarantee.
    """

    unconfined = UnconfinedProvider()
    registry = ProviderRegistry([unconfined])

    with pytest.raises(UnsatisfiableRequirementError) as info:
        registry.select(_requirement(isolation="required"))

    assert "isolation was required" in str(info.value)
    assert "unconfined" in str(info.value)
    assert unconfined.evaluate(_requirement(isolation="required")).satisfied is False


def test_required_isolation_denies_before_any_weaker_provider_runs() -> None:
    """A real, working, but *insufficient* provider must not be substituted.

    The stub cannot confine and says so; a weaker provider is not a fallback for
    a required guarantee, it is the thing the deny exists to prevent.
    """

    weak = _StubProvider(
        "weak",
        enforcement="partial",
        limitations=("cannot_deny_truncate",),
        satisfied=False,
        reason="old ABI cannot deny truncate",
    )
    registry = with_unconfined_fallback([weak])

    with pytest.raises(UnsatisfiableRequirementError) as info:
        registry.select(_requirement(isolation="required"))

    assert "old ABI cannot deny truncate" in str(info.value)
    assert weak.confine_calls == [], "a denied selection must not run the weaker provider"
    assert isinstance(registry.providers()[-1], UnconfinedProvider)


def test_a_required_request_is_satisfied_when_a_backend_can_meet_it() -> None:
    strong = _StubProvider("strong", enforcement="full")
    unconfined = UnconfinedProvider()

    selection = ProviderRegistry([strong, unconfined]).select(_requirement(isolation="required"))

    assert selection.provider.provider_id == "strong"
    assert selection.evaluation.enforcement == "full"


# --------------------------------------------------------------------------
# The unconfined fallback
# --------------------------------------------------------------------------


def test_the_unconfined_fallback_is_used_when_isolation_is_not_required() -> None:
    unconfined = UnconfinedProvider()
    registry = ProviderRegistry([unconfined])

    selection = registry.select(_requirement(isolation="preferred"))

    assert selection.provider.provider_id == "unconfined"
    assert selection.evaluation.enforcement == "none"


def test_the_unconfined_fallback_reports_none_rather_than_claiming_a_boundary() -> None:
    result = UnconfinedProvider().confine(
        ("true",), ExecutionPolicy(mode="workspace-write", workspace_root="/tmp")
    )

    assert result.argv == ("true",)
    assert result.enforcement == "none"
    assert result.denial_signatures == ()
    assert result.runner_failure_rules == ()


def test_the_unconfined_fallback_never_beats_a_real_backend() -> None:
    real = _StubProvider("real", enforcement="partial", limitations=("cannot_deny_truncate",))
    unconfined = UnconfinedProvider()

    selection = ProviderRegistry([unconfined, real]).select(_requirement())

    assert selection.provider.provider_id == "real"


def test_the_fallback_is_registered_last() -> None:
    real = _StubProvider("real")
    registry = with_unconfined_fallback([real])

    assert [provider.provider_id for provider in registry.providers()] == ["real", "unconfined"]


def test_an_empty_registry_denies() -> None:
    with pytest.raises(UnsatisfiableRequirementError, match="no providers registered"):
        ProviderRegistry().select(_requirement())


# --------------------------------------------------------------------------
# Contract enforcement during selection
# --------------------------------------------------------------------------


def test_a_partial_provider_must_name_its_limitation() -> None:
    """A defective provider surfaces as a defect, not as a normal outcome."""

    defective = _StubProvider("defective", enforcement="partial", limitations=())

    with pytest.raises(ProviderContractError, match="no declared limitation"):
        ProviderRegistry([defective]).select(_requirement())


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def test_the_rendered_selection_names_the_choice_and_the_candidates() -> None:
    satisfied = _StubProvider("good", reason="works")
    rejected = _StubProvider("bad", satisfied=False, reason="no backend")

    rendered = render_selection(ProviderRegistry([satisfied, rejected]).select(_requirement()))

    assert "selected good" in rendered
    assert "enforcement=full" in rendered
    assert "good" in rendered
    assert "bad" in rendered
    assert "no backend" in rendered


def test_the_rendered_selection_lists_limitations() -> None:
    limited = _StubProvider(
        "limited", enforcement="partial", limitations=("cannot_deny_truncate",)
    )

    rendered = render_selection(ProviderRegistry([limited]).select(_requirement()))

    assert "cannot_deny_truncate" in rendered
