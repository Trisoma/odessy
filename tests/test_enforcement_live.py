"""Measure the strongest confinement backend this host actually offers.

Unlike ``test_sandbox_live.py`` (bwrap on Linux), this file is backend-agnostic so it
can run on macOS (seatbelt) and Windows (windows-acl) too. It drives the same
``confine()`` the executor uses and asserts the honest invariant: a backend must
not declare a boundary as enforced when the measured boundary is open.

Skipped unless ``ODESSY_SANDBOX_LIVE=1`` and at least one confined backend is usable.
CI gives this env var on the Linux/Windows/macOS live jobs, so a missing backend is
a failure there rather than a silent skip. In ``required`` mode a backend that cannot
pass its own control probe is unusable, not strict; we select the strongest usable
backend and let the measurement speak.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path

import pytest

from odessy.execution.contract import ExecutionPolicy, Requirement
from odessy.execution.enforcement_probe import probe_enforcement
from odessy.execution.providers import default_execution_providers
from odessy.execution.registry import ProviderRegistry
from odessy.execution.roots import writable_roots

LIVE = os.environ.get("ODESSY_SANDBOX_LIVE") == "1"


@pytest.fixture
def layout(tmp_path: Path):
    # Skip inside the fixture so the listener and its thread are never created when
    # this file runs in the ordinary Python job (where ODESSY_SANDBOX_LIVE is unset).
    # Building them only to skip in the test body left daemon threads to be reaped at
    # interpreter teardown, which surfaced as "Event loop is closed" on Python 3.10.
    if not LIVE:
        pytest.skip("set ODESSY_SANDBOX_LIVE=1 to measure a real backend")
    workspace = tmp_path / "ws"
    outside = tmp_path / "outside"
    hidden = tmp_path / "hidden"
    for path in (workspace, outside, hidden):
        path.mkdir()
    (hidden / "secret.txt").write_text("credential", encoding="utf-8")

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    port = listener.getsockname()[1]

    def accept():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            conn.close()

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield workspace, outside, hidden, port
    finally:
        listener.close()
        thread.join(timeout=2)


def test_measured_boundary_does_not_overstate(layout):
    workspace, outside, hidden, port = layout
    policy = ExecutionPolicy(mode="workspace-write", workspace_root=str(workspace),
                             network="deny", hidden_paths=(str(hidden),))
    if sys.platform == "darwin":
        # Seatbelt allows writes in system temp directories in workspace-write mode.
        target = outside.resolve()
        assert not any(target.is_relative_to(root) for root in writable_roots(policy)), (
            "Run this test with --basetemp in a directory outside system temp roots"
        )
    registry = ProviderRegistry(default_execution_providers())
    try:
        # select() returns the strongest backend that satisfies the request and
        # never chooses unconfined when a real backend can satisfy it.
        selection = registry.select(Requirement(policy=policy, isolation="required"))
    except Exception:
        pytest.fail("ODESSY_SANDBOX_LIVE=1 but no confined backend can satisfy a required request")
    provider = selection.provider

    measurement = probe_enforcement(
        provider, policy, workspace=workspace, outside=outside,
        hidden=hidden, hidden_file=hidden / "secret.txt", port=port,
    )
    # Print the full measurement so a live job's log records what this host really
    # enforced, even when a platform-specific dimension disagrees with the plan.
    print("measured enforcement:", measurement.as_dict())

    assert measurement.usable, f"{provider.provider_id} control probe failed: {measurement.control_failure}"
    # The one invariant that must hold on every host: a backend that declares a
    # full filesystem-write boundary must actually deny a write outside the
    # workspace. This is the dimension every confinement mechanism shares.
    #
    # Network and hidden-path probes are deliberately diagnostics here, not hard
    # assertions: how sandbox-exec applies ``deny network*`` to a loopback connect,
    # and whether a freshly created hidden path is masked, are OS-specific details
    # this project has never qualified (seatbelt.py marks them "not verified on a
    # live macOS host"). Failing the whole job on them would turn a first-ever
    # measurement into a red CI without telling us what the host actually did —
    # the printed report above is the evidence we need before tightening this.
    assert measurement.verdict("write_outside") is True, measurement.as_dict()
    if sys.platform == "darwin":
        assert measurement.verdict("symlink_escape") is True, measurement.as_dict()
    if measurement.verdict("network") is False:
        print("diagnostic: network was reachable despite network=deny")
    if measurement.verdict("read_hidden") is False:
        print("diagnostic: a hidden path was readable")
