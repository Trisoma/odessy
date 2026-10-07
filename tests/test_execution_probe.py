"""Host discovery.

The pure classifiers are tested directly, because the states they map are the
whole point: four bwrap situations that must not collapse into one, and a
Landlock ABI that is reported rather than reduced to a boolean.
"""

from __future__ import annotations

import sys

import pytest

from odessy.execution.probe import (
    PROBE_MARKER_READONLY,
    PROBE_MARKER_WRITABLE,
    BwrapFacts,
    HostFacts,
    bwrap_probe_argv,
    bwrap_probe_script,
    classify_bwrap_availability,
    discover_host_facts,
    probe_bwrap,
    probe_device_nodes,
    probe_landlock,
    probe_landlock_launcher,
    probe_seatbelt,
    probe_user_namespaces,
    render_host_facts,
    reset_host_facts,
)

_ON_LINUX = sys.platform == "linux"


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    """Discovery is cached per process; each test starts from a clean cache."""

    reset_host_facts()
    yield
    reset_host_facts()


# --------------------------------------------------------------------------
# bwrap: four distinguishable states
# --------------------------------------------------------------------------


def test_no_binary_is_its_own_state() -> None:
    assert classify_bwrap_availability(None, is_setuid_root=False, probe_exit_code=None) == (
        "unavailable_no_binary"
    )


def test_a_working_unprivileged_binary_is_userns() -> None:
    assert classify_bwrap_availability(
        "/usr/bin/bwrap", is_setuid_root=False, probe_exit_code=0
    ) == "available_userns"


def test_a_working_setuid_binary_is_its_own_state() -> None:
    """setuid-root means a bwrap flaw is local privilege escalation.

    That is materially different from the unprivileged path, so it must not
    share a state with it.
    """

    assert classify_bwrap_availability(
        "/usr/bin/bwrap", is_setuid_root=True, probe_exit_code=0
    ) == "available_setuid_root"


def test_an_installed_but_failing_binary_is_its_own_state() -> None:
    """The state that most often masquerades as "available".

    The binary exists, so a naive presence check says yes; the functional probe
    fails, so nothing is actually confined.
    """

    assert classify_bwrap_availability(
        "/usr/bin/bwrap", is_setuid_root=False, probe_exit_code=1
    ) == "unavailable_binary_unusable"


def test_a_timed_out_probe_is_not_available() -> None:
    """``None`` means the probe could not produce an exit code."""

    assert classify_bwrap_availability(
        "/usr/bin/bwrap", is_setuid_root=False, probe_exit_code=None
    ) == "unavailable_binary_unusable"


def test_bwrap_probe_argv_attempts_a_prohibited_write_inside_the_profile() -> None:
    """The probe must attempt a write, not merely spawn a command.

    Upstream's probe ran a bare ``true``, which proves bwrap can assemble a
    profile but not that the profile denies anything. The write attempt is what
    separates an enforcement boundary from a successful spawn.
    """

    argv = bwrap_probe_argv("/usr/bin/bwrap", "/tmp/odessy-probe/write")

    assert argv[0] == "/usr/bin/bwrap"
    assert "--ro-bind" in argv
    assert "--unshare-pid" in argv
    assert "--die-with-parent" in argv
    # The separator must precede the wrapped command, as it will at runtime.
    assert argv.index("--") < argv.index("/bin/sh")
    assert "/tmp/odessy-probe/write" in argv[-1]


def test_bwrap_probe_script_reports_both_outcomes_distinguishably() -> None:
    """The script must report which happened, not merely exit.

    A probe that inspects only the exit status cannot tell "the boundary held"
    from "bwrap failed to run the command", and the latter is the common case.
    """

    script = bwrap_probe_script("/tmp/odessy-probe/write")

    assert PROBE_MARKER_READONLY in script
    assert PROBE_MARKER_WRITABLE in script
    assert PROBE_MARKER_READONLY != PROBE_MARKER_WRITABLE


def test_bwrap_probe_script_quotes_the_target() -> None:
    script = bwrap_probe_script("/tmp/a path with spaces/write")

    assert "'/tmp/a path with spaces/write'" in script or '"/tmp/a path with spaces/write"' in script


# --------------------------------------------------------------------------
# Real probes on the running host
# --------------------------------------------------------------------------


def test_landlock_reports_an_abi_or_a_reason() -> None:
    facts = probe_landlock()

    if facts.abi is None:
        assert facts.detail, "an absent ABI must carry the reason it is absent"
    else:
        assert facts.abi > 0


def test_landlock_is_linux_only() -> None:
    if not _ON_LINUX:
        assert probe_landlock().abi is None


def test_bwrap_reports_one_of_the_four_states() -> None:
    facts = probe_bwrap()

    assert facts.availability in {
        "unavailable_no_binary",
        "unavailable_binary_unusable",
        "available_userns",
        "available_setuid_root",
    }
    assert facts.detail


def test_bwrap_is_unavailable_off_linux() -> None:
    if not _ON_LINUX:
        assert probe_bwrap().availability == "unavailable_no_binary"


def test_seatbelt_is_unavailable_off_macos() -> None:
    if sys.platform != "darwin":
        facts = probe_seatbelt()
        assert facts.available is False
        assert facts.detail


def test_landlock_launcher_absence_is_stated_plainly() -> None:
    """Odessy does not ship the native launcher, so its absence must say so."""

    facts = probe_landlock_launcher()

    if facts.path is None:
        assert facts.enforcement is None
        assert "does not ship" in facts.detail
    else:
        # Present: the launcher was asked to prove enforcement, not merely exist.
        assert facts.detail


def test_user_namespace_sysctls_are_read_or_absent() -> None:
    facts = probe_user_namespaces()

    # None means the key is absent, which is different from a key set to zero.
    for value in (
        facts.unprivileged_userns_clone,
        facts.max_user_namespaces,
        facts.apparmor_restrict_unprivileged_userns,
    ):
        assert value is None or isinstance(value, (bool, int))


def test_device_nodes_are_discovered_as_a_mapping() -> None:
    nodes = probe_device_nodes()

    for capability, paths in nodes.items():
        assert paths, f"{capability} was recorded with no paths"
        assert list(paths) == sorted(paths)


# --------------------------------------------------------------------------
# Caching and reporting
# --------------------------------------------------------------------------


def test_discovery_is_cached_within_a_process() -> None:
    """Discovery execs processes and issues a syscall, so it runs once."""

    first = discover_host_facts()
    second = discover_host_facts()

    assert first is second


def test_resetting_the_cache_forces_rediscovery() -> None:
    first = discover_host_facts()
    reset_host_facts()
    second = discover_host_facts()

    assert first is not second
    assert isinstance(second, HostFacts)


def test_the_report_names_every_discovered_surface() -> None:
    text = render_host_facts(discover_host_facts())

    assert "host" in text
    assert "landlock" in text
    assert "bwrap" in text
    assert "seatbelt" in text
    assert "user namespaces" in text


def test_the_report_says_when_nothing_was_discovered() -> None:
    rendered = render_host_facts(
        HostFacts(
            platform="test",
            system="Test",
            release="0",
            machine="none",
            landlock=probe_landlock(),
            landlock_launcher=probe_landlock_launcher(),
            bwrap=BwrapFacts(
                availability="unavailable_no_binary",
                path=None,
                is_setuid_root=False,
                version=None,
                detail="absent",
            ),
            seatbelt=probe_seatbelt(),
            user_namespaces=probe_user_namespaces(),
            device_nodes={},
        )
    )

    assert "none discovered" in rendered
