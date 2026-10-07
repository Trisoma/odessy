"""Provider evaluation and confinement.

This is where the actual security guarantees are asserted. Four properties carry
the weight:

1. The Landlock limitation set is *derived* from the negotiated ABI, using the
   boundaries the kernel header defines.
2. ``partial`` is reported only when a promised effect is genuinely ungoverned,
   and a scope note never blocks a request that did not ask about it.
3. ``required`` isolation is met only by ``full`` enforcement.
4. An unusable backend refuses to confine rather than returning the caller's argv.
"""

from __future__ import annotations

import pytest

from odessy.execution.contract import (
    ExecutionPolicy,
    ProviderUnavailableError,
    Requirement,
)
from odessy.execution.diagnostics import classify_runner_failure, matches_signature
from odessy.execution.probe import (
    BwrapFacts,
    HostFacts,
    LandlockFacts,
    LandlockLauncherFacts,
    SeatbeltFacts,
    UserNamespaceFacts,
)
from odessy.execution.providers import (
    BwrapProvider,
    LandlockProvider,
    SeatbeltProvider,
    default_execution_providers,
)

_LAUNCHER = "/usr/bin/landlock-run"


def _facts(
    *,
    abi: int | None = 5,
    launcher_enforcement: str | None = "full",
    launcher_path: str | None = _LAUNCHER,
    bwrap_availability: str = "unavailable_no_binary",
    bwrap_path: str | None = None,
    seatbelt_available: bool = False,
    seatbelt_path: str | None = None,
) -> HostFacts:
    return HostFacts(
        platform="linux",
        system="Linux",
        release="6.7.0",
        machine="aarch64",
        landlock=LandlockFacts(abi=abi, detail=f"stub: ABI {abi}"),
        landlock_launcher=LandlockLauncherFacts(
            path=launcher_path,
            enforcement=launcher_enforcement,  # type: ignore[arg-type]
            detail="stub launcher",
        ),
        bwrap=BwrapFacts(
            availability=bwrap_availability,  # type: ignore[arg-type]
            path=bwrap_path,
            is_setuid_root=False,
            version="bubblewrap 0.8.0",
            detail="stub bwrap",
        ),
        seatbelt=SeatbeltFacts(
            available=seatbelt_available, path=seatbelt_path, detail="stub seatbelt"
        ),
        user_namespaces=UserNamespaceFacts(None, None, None),
        device_nodes={},
    )


def _requirement(
    *,
    mode: str = "workspace-write",
    isolation: str = "preferred",
    capabilities: tuple[str, ...] = ("process-exec",),
) -> Requirement:
    return Requirement(
        policy=ExecutionPolicy(mode=mode, workspace_root="/work"),  # type: ignore[arg-type]
        isolation=isolation,  # type: ignore[arg-type]
        capabilities=frozenset(capabilities),  # type: ignore[arg-type]
    )


def _usable_bwrap() -> BwrapProvider:
    return BwrapProvider(
        facts=_facts(bwrap_availability="available_userns", bwrap_path="/usr/bin/bwrap"),
        executable="/usr/bin/bwrap",
    )


def _usable_landlock(**kwargs) -> LandlockProvider:  # type: ignore[no-untyped-def]
    return LandlockProvider(facts=_facts(**kwargs), launcher=_LAUNCHER)


def _usable_seatbelt() -> SeatbeltProvider:
    return SeatbeltProvider(
        facts=_facts(seatbelt_available=True, seatbelt_path="/usr/bin/sandbox-exec"),
        executable="/usr/bin/sandbox-exec",
    )


# --------------------------------------------------------------------------
# 1. The Landlock limitation set is derived from the negotiated ABI
# --------------------------------------------------------------------------

_GAPS_BY_ABI = {
    # ABI 1: no REFER (bit 13), no TRUNCATE (bit 14), no IOCTL_DEV (bit 15)
    1: {"cannot_deny_truncate", "cannot_restrict_rename_between_dirs", "cannot_restrict_ioctl"},
    # ABI 2 added REFER
    2: {"cannot_deny_truncate", "cannot_restrict_ioctl"},
    # ABI 3 added TRUNCATE
    3: {"cannot_restrict_ioctl"},
    # ABI 4 added TCP bits only, so no filesystem right changed
    4: {"cannot_restrict_ioctl"},
    # ABI 5 added IOCTL_DEV
    5: set(),
}


@pytest.mark.parametrize(("abi", "expected"), sorted(_GAPS_BY_ABI.items()))
def test_landlock_gaps_track_the_kernel_header(abi: int, expected: set[str]) -> None:
    """Each boundary comes from the header, so each must be pinned here.

    A limitation that drifts from the ABI it describes is worse than no
    limitation: it reads as a considered statement about this host.
    """

    provider = _usable_landlock(abi=abi)
    reported = provider.limitations(ExecutionPolicy(mode="read-only", workspace_root="/work"))

    assert set(reported) & provider.GAP_LIMITATIONS == expected


def test_landlock_abi_five_reports_full_enforcement() -> None:
    """The newest known ABI governs every right the profile asks for."""

    evaluation = _usable_landlock(abi=5).evaluate(_requirement())

    assert evaluation.enforcement == "full"
    assert evaluation.satisfied is True


@pytest.mark.parametrize("abi", [1, 2, 3, 4])
def test_landlock_older_abis_report_partial(abi: int) -> None:
    assert _usable_landlock(abi=abi).evaluate(_requirement()).enforcement == "partial"


def test_landlock_truncate_gap_is_real_before_abi_three() -> None:
    """Before ABI 3 ``truncate`` is not mediated.

    A process under ``read-only`` can therefore still truncate a file it has
    filesystem write permission for, which is why this is a gap rather than a
    note.
    """

    assert "cannot_deny_truncate" in _usable_landlock(abi=2).limitations(
        ExecutionPolicy(mode="read-only", workspace_root="/work")
    )
    assert "cannot_deny_truncate" not in _usable_landlock(abi=3).limitations(
        ExecutionPolicy(mode="read-only", workspace_root="/work")
    )


# --------------------------------------------------------------------------
# 2. Scope notes versus shortfalls
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider", [_usable_bwrap(), _usable_seatbelt()], ids=["bwrap", "seatbelt"]
)
def test_a_backend_with_no_shortfall_reports_full(provider) -> None:  # type: ignore[no-untyped-def]
    """Both govern every file effect the mode promises.

    Their remaining limitations lie outside the file-effect vocabulary, so they
    are scope notes and do not make enforcement partial.
    """

    evaluation = provider.evaluate(_requirement())

    assert evaluation.enforcement == "full"
    assert evaluation.limitations, "scope notes are still reported under full"


def test_bwrap_reports_no_process_visibility_limitation() -> None:
    """``--unshare-pid`` genuinely hides host processes, so it is not a gap.

    Reporting one anyway would be as dishonest as omitting a real one.
    """

    assert "cannot_restrict_process_visibility" not in _usable_bwrap().limitations(
        ExecutionPolicy(mode="read-only", workspace_root="/work")
    )


def test_bwrap_reports_no_device_limitation() -> None:
    """A fresh ``/dev`` shadows the host's device nodes entirely."""

    assert "cannot_restrict_device_access" not in _usable_bwrap().limitations(
        ExecutionPolicy(mode="read-only", workspace_root="/work")
    )


def test_a_scope_note_does_not_block_a_request_that_ignored_it() -> None:
    """bwrap cannot restrict network, but a request that never asked is fine."""

    assert _usable_bwrap().evaluate(_requirement()).satisfied is True


def test_a_scope_note_blocks_a_request_that_asked_for_that_dimension() -> None:
    """A request that *did* ask is answered honestly rather than quietly met."""

    evaluation = _usable_bwrap().evaluate(_requirement(capabilities=("process-exec", "network")))

    assert evaluation.satisfied is False
    assert "cannot_restrict_network" in evaluation.reason


# --------------------------------------------------------------------------
# 3. required means an absolute boundary
# --------------------------------------------------------------------------


@pytest.mark.parametrize("abi", [1, 2, 3, 4])
def test_required_isolation_is_not_met_by_partial_enforcement(abi: int) -> None:
    evaluation = _usable_landlock(abi=abi).evaluate(_requirement(isolation="required"))

    assert evaluation.satisfied is False
    assert "required isolation" in evaluation.reason
    assert evaluation.enforcement == "partial"


def test_required_isolation_is_met_by_full_enforcement() -> None:
    evaluation = _usable_landlock(abi=5).evaluate(_requirement(isolation="required"))

    assert evaluation.satisfied is True


def test_preferring_isolation_accepts_partial_enforcement() -> None:
    """``partial`` is an acceptable answer to a preference, and only to a preference."""

    assert _usable_landlock(abi=3).evaluate(_requirement(isolation="preferred")).satisfied is True


# --------------------------------------------------------------------------
# 4. Fail-closed
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider",
    [
        BwrapProvider(facts=_facts(bwrap_availability="unavailable_no_binary")),
        BwrapProvider(facts=_facts(bwrap_availability="unavailable_binary_unusable")),
        LandlockProvider(facts=_facts(abi=None, launcher_enforcement=None, launcher_path=None)),
        LandlockProvider(facts=_facts(abi=5, launcher_enforcement=None)),
        SeatbeltProvider(facts=_facts(seatbelt_available=False)),
    ],
    ids=["no-binary", "binary-unusable", "no-launcher", "no-abi", "no-seatbelt"],
)
def test_an_unusable_backend_refuses_to_confine(provider) -> None:  # type: ignore[no-untyped-def]
    """It must raise, never return the argv.

    A caller that receives an argv has no way to tell a wrapped one from an
    unwrapped one, so returning the caller's argv here would be exactly the
    silent unconfined passthrough the contract forbids.
    """

    with pytest.raises(ProviderUnavailableError):
        provider.confine(
            ["echo", "hi"], ExecutionPolicy(mode="read-only", workspace_root="/work")
        )

    assert provider.probe().available is False


def test_an_unusable_backend_is_never_satisfied() -> None:
    evaluation = BwrapProvider(facts=_facts()).evaluate(_requirement())

    assert evaluation.satisfied is False
    assert evaluation.enforcement == "none"


def test_confining_under_full_access_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="requests no confinement"):
        _usable_bwrap().confine(
            ["echo"], ExecutionPolicy(mode="danger-full-access", workspace_root="/work")
        )


# --------------------------------------------------------------------------
# Confinement output
# --------------------------------------------------------------------------


def test_confine_returns_the_wrapped_argv_and_the_backend_dialect() -> None:
    result = _usable_bwrap().confine(
        ["echo", "hi"], ExecutionPolicy(mode="read-only", workspace_root="/work")
    )

    assert result.provider_id == "bwrap"
    assert result.argv[0] == "/usr/bin/bwrap"
    assert result.argv[-2:] == ("echo", "hi")
    assert result.denial_signatures == ("read-only file system",)
    assert result.enforcement == "full"


def test_landlock_confine_declares_the_launcher_failure_rule() -> None:
    """Both signals are required: exit 125 *and* a fatal ``landlock-run:`` line.

    A successfully exec'd child may itself return 125, so the exit code alone is
    never evidence of launcher failure.
    """

    result = _usable_landlock(abi=5).confine(
        ["echo", "hi"], ExecutionPolicy(mode="read-only", workspace_root="/work")
    )

    assert result.argv[:4] == (_LAUNCHER, "--ro", "/", "--rw")
    assert result.denial_signatures == ("permission denied",)

    (rule,) = result.runner_failure_rules
    assert rule.allowed_exit_codes == (125,)
    assert rule.fatal_signatures == ("landlock-run: ",)

    # The older-ABI notice is informational, not fatal evidence.
    assert classify_runner_failure(
        125, "landlock-run: partial enforcement (older Landlock ABI)\n", result.runner_failure_rules
    ) is None
    assert classify_runner_failure(
        125, "landlock-run: ruleset error\n", result.runner_failure_rules
    ) is not None
    # A clean run is never mistaken for a runner failure.
    assert classify_runner_failure(0, "", result.runner_failure_rules) is None


def test_a_denial_is_distinguished_from_a_runner_failure() -> None:
    """Confinement working is not the same as the runner being broken."""

    result = _usable_landlock(abi=5).confine(
        ["echo"], ExecutionPolicy(mode="read-only", workspace_root="/work")
    )

    assert matches_signature(1, "sh: cannot create /work/x: Permission denied", result.denial_signatures)
    assert classify_runner_failure(
        1, "sh: cannot create /work/x: Permission denied\n", result.runner_failure_rules
    ) is None


def test_the_default_provider_list_is_ordered_by_strength() -> None:
    """Order breaks ties among satisfied providers; it never creates satisfaction."""

    assert [provider.provider_id for provider in default_execution_providers()] == [
        "bwrap",
        "landlock",
        "seatbelt",
    ]
