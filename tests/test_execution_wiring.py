"""The executor seam: every command is confined, or says why it was not.

Phase 7 wires `CommandBridge` through the execution layer. Two properties matter,
and both are tested here rather than assumed:

1. **The argv that actually runs is the one `confine()` returned.** A bridge that
   confined for show and then spawned the original shell command would pass every
   test that only inspects the report. The rewriting provider below closes that
   gap by making the two argvs produce different output.
2. **A run always states its own enforcement.** "Unconfined" and "confined, but
   only partially" are different answers, and a caller that cannot tell them apart
   cannot act on either.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from odessy.command_bridge import CommandBridge, TerminalRunResult
from odessy.execution.contract import (
    ConfineResult,
    ExecutionPolicy,
    ProviderUnavailableError,
)


class _RewritingProvider:
    """A backend that replaces the argv, so a test can see that it took effect.

    Minimal on purpose: the bridge only ever calls `confine()` and then reads
    `provider_id`, `enforcement`, and `limitations` off the result.
    """

    provider_id = "test-rewriting"

    def __init__(
        self,
        *,
        script: str = "print('from-the-confinement')",
        enforcement: str = "full",
        limitations: frozenset[str] = frozenset(),
    ) -> None:
        self._argv = (sys.executable, "-c", script)
        self.enforcement = enforcement
        self.limitations = limitations

    def confine(self, argv: object, policy: object) -> ConfineResult:
        return ConfineResult(
            argv=self._argv,
            enforcement=self.enforcement,  # type: ignore[arg-type]
            limitations=frozenset(self.limitations),  # type: ignore[arg-type]
            denial_signatures=(),
            runner_failure_rules=(),
            provider_id=self.provider_id,
        )


class _UnavailableProvider:
    """A backend that cannot confine on this host."""

    provider_id = "test-unavailable"

    def confine(self, argv: object, policy: object) -> ConfineResult:
        raise ProviderUnavailableError("this host cannot confine")


class _MissingExecutableProvider:
    """A backend that confines to an executable which does not exist."""

    provider_id = "test-missing-executable"

    def confine(self, argv: object, policy: object) -> ConfineResult:
        return ConfineResult(
            argv=("odessy-definitely-not-a-real-executable-xyz",),
            enforcement="full",
            limitations=frozenset(),
            denial_signatures=(),
            runner_failure_rules=(),
            provider_id=self.provider_id,
        )


def _policy(workspace: Path, mode: str = "workspace-write") -> ExecutionPolicy:
    return ExecutionPolicy(mode=mode, workspace_root=str(workspace))  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The argv that runs is the one confine() returned
# --------------------------------------------------------------------------


def test_the_confined_argv_is_what_runs(tmp_path: Path) -> None:
    bridge = CommandBridge(
        tmp_path, provider=_RewritingProvider(), policy=_policy(tmp_path)
    )

    result = bridge.run(command="echo original", cwd=".")

    assert result["exit_code"] == 0
    assert "from-the-confinement" in result["stdout"]
    # The shell command the bridge built must NOT have run.
    assert "original" not in result["stdout"]


def test_the_report_names_the_backend_and_its_completeness(tmp_path: Path) -> None:
    bridge = CommandBridge(
        tmp_path,
        provider=_RewritingProvider(
            enforcement="partial", limitations=frozenset({"cannot_restrict_reads"})
        ),
        policy=_policy(tmp_path),
    )

    result = bridge.run(command="echo hi")

    assert result["sandbox"]["provider_id"] == "test-rewriting"
    assert result["sandbox"]["enforcement"] == "partial"
    assert result["sandbox"]["limitations"] == ["cannot_restrict_reads"]


def test_no_backend_is_reported_rather_than_implied(tmp_path: Path) -> None:
    result = CommandBridge(tmp_path).run(command="echo hi")

    # The command still runs, and the result states exactly what confined it. The
    # absence is loud: a caller never has to infer enforcement from silence.
    assert result["exit_code"] == 0
    assert result["sandbox"] == {
        "provider_id": "unconfined",
        "enforcement": "none",
        "limitations": [],
    }


def test_a_timeout_still_reports_the_confinement(tmp_path: Path) -> None:
    # Both return paths carry the report: a caller reading a timed-out result needs
    # to know what confined it just as much as one reading a success.
    bridge = CommandBridge(
        tmp_path,
        provider=_RewritingProvider(script="import time; time.sleep(30)"),
        policy=_policy(tmp_path),
    )

    result = bridge.run(command="echo hi", timeout=1)

    assert result["timed_out"] is True
    assert result["status"] == "timeout"
    assert result["exit_code"] is None
    assert result["sandbox"]["provider_id"] == "test-rewriting"


def test_the_result_still_carries_every_declared_key(tmp_path: Path) -> None:
    # `TerminalRunResult.__annotations__` merges the optional base, so moving
    # `sandbox` into that base makes this cover it too.
    result = CommandBridge(tmp_path).run(command="echo hi")

    assert set(TerminalRunResult.__annotations__) <= set(result)


# --------------------------------------------------------------------------
# Fail-closed
# --------------------------------------------------------------------------


def test_an_unavailable_backend_stops_the_command(tmp_path: Path) -> None:
    # It must raise, never fall back to running unconfined: a caller that asked for
    # a boundary would otherwise get a result indistinguishable from one where the
    # boundary held.
    bridge = CommandBridge(
        tmp_path, provider=_UnavailableProvider(), policy=_policy(tmp_path)
    )

    with pytest.raises(ProviderUnavailableError):
        bridge.run(command="echo hi")


def test_a_policy_without_a_backend_is_rejected(tmp_path: Path) -> None:
    # A policy on its own confines nothing, so accepting it would let a caller
    # believe a boundary was configured when none was.
    with pytest.raises(ValueError, match="together"):
        CommandBridge(tmp_path, policy=_policy(tmp_path))


def test_a_backend_without_a_policy_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="together"):
        CommandBridge(tmp_path, provider=_RewritingProvider())


def test_the_mcp_server_threads_the_backend_through(tmp_path: Path) -> None:
    # `build_odessy_mcp_server` must not construct a bare bridge: doing so would
    # make confinement unreachable from the tool surface however well the layers
    # below it worked.
    from odessy.mcp_server import build_odessy_mcp_server

    # An unpaired provider reaches `CommandBridge.__post_init__`, which rejects it.
    # That the rejection surfaces proves the argument is threaded rather than
    # dropped — a bare `CommandBridge(...)` would swallow it silently.
    with pytest.raises(ValueError, match="together"):
        build_odessy_mcp_server(workspace_root=tmp_path, provider=_RewritingProvider())

    server = build_odessy_mcp_server(
        workspace_root=tmp_path,
        provider=_RewritingProvider(),
        policy=_policy(tmp_path),
    )

    assert server is not None


# --------------------------------------------------------------------------
# What `exit_code` means
# --------------------------------------------------------------------------
#
# The contract distinguishes a *process outcome* from *executor termination*:
#
#   command exits 0     -> exit_code 0,    timed_out False, status "ok"
#   command exits 7     -> exit_code 7,    timed_out False, status "failed"
#   executor timeout    -> exit_code None, timed_out True,  status "timeout"
#   spawn failure       -> raises; there is no result to describe
#
# `None` rather than a sentinel because a sentinel is indistinguishable from a real
# exit code, and so reads as the command's own outcome. A process the executor killed
# has no exit status, and Windows' incidental kill code (1) is not the command's
# result. Node pins the identical matrix; the two must not drift, because everything
# above this layer reads the same field.


def test_a_successful_command_reports_its_exit_code(tmp_path: Path) -> None:
    result = CommandBridge(tmp_path).run(command="exit 0")

    assert result["exit_code"] == 0
    assert result["timed_out"] is False
    assert result["status"] == "ok"


def test_a_failing_command_reports_its_real_code(tmp_path: Path) -> None:
    # A real non-zero status is preserved as-is: this is the case a sentinel would
    # collide with if the sentinel were a plausible exit code.
    result = CommandBridge(tmp_path).run(command="exit 7")

    assert result["exit_code"] == 7
    assert result["timed_out"] is False
    assert result["status"] == "failed"


def test_a_timeout_reports_no_exit_code_at_all(tmp_path: Path) -> None:
    # A REAL long-running process, on the REAL timeout path. The rewriting provider
    # changes which command runs; it does not stub the timeout mechanism, so
    # `communicate(timeout=...)` genuinely expires and the termination path runs.
    marker = tmp_path / "survived.txt"
    script = (
        "import pathlib, time\n"
        "time.sleep(2)\n"
        f"pathlib.Path({str(marker)!r}).write_text('survived')\n"
    )
    bridge = CommandBridge(
        tmp_path,
        provider=_RewritingProvider(script=script),
        policy=_policy(tmp_path),
    )

    started = time.monotonic()
    result = bridge.run(command="echo hi", timeout=1)
    elapsed = time.monotonic() - started

    # Three separate claims, so a regression in one names itself rather than hiding
    # behind the others.
    assert result["exit_code"] is None
    assert result["timed_out"] is True
    assert result["status"] == "timeout"

    # Returning well before the child's own sleep elapsed is something waiting the
    # process out could not have achieved.
    assert elapsed < 2.5, f"returned after {elapsed:.1f}s, which suggests no termination"

    # The load-bearing assertion. If termination silently failed, the child would exit
    # on its own and every assertion above would still hold — the flags would be set
    # and the elapsed bound satisfied. This marker is written only by a process that
    # survived to finish its sleep.
    time.sleep(2.0)
    assert not marker.exists(), "the confined process survived its own termination"


def test_the_termination_marker_can_appear_at_all(tmp_path: Path) -> None:
    # The control for the test above. Without it, "the marker is absent" could mean the
    # process was terminated OR that the script never ran. Here the same script runs to
    # completion, so the absence above is attributable to termination and nothing else.
    marker = tmp_path / "survived.txt"
    script = (
        "import pathlib, time\n"
        "time.sleep(1)\n"
        f"pathlib.Path({str(marker)!r}).write_text('survived')\n"
    )
    bridge = CommandBridge(
        tmp_path,
        provider=_RewritingProvider(script=script),
        policy=_policy(tmp_path),
    )

    result = bridge.run(command="echo hi", timeout=10)

    assert result["timed_out"] is False
    assert result["exit_code"] == 0
    assert result["status"] == "ok"
    assert marker.exists(), "the marker mechanism itself is broken"


def test_a_spawn_failure_produces_no_result_at_all(tmp_path: Path) -> None:
    # It is not a result carrying a null exit code: the command never ran, so there
    # is no outcome to describe. Raising keeps "the command failed" and "the command
    # never started" distinct, which a synthetic result would blur.
    bridge = CommandBridge(
        tmp_path, provider=_MissingExecutableProvider(), policy=_policy(tmp_path)
    )

    with pytest.raises(OSError):
        bridge.run(command="echo hi")
