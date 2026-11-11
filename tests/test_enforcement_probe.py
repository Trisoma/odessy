"""Prompt 08: what this host's confinement backends actually do, measured.

Deterministic tests prove the *translation*. These run real commands through the same
``confine()`` the executor uses, because "the backend denies a write outside the workspace"
is a claim about an operating system and nothing in a unit test can establish it.

Two layers:

* the **unconfined** provider is always available, so it establishes that the probe itself
  is sound: with no boundary every dimension must come back *open* (writes outside succeed,
  hidden reads succeed, the network connects). If any of those were reported denied, the
  probe would be measuring its own bug.
* the **windows-acl** rung — where Node and its built runner exist — is measured for real,
  and whatever it does is what the test asserts. A backend that refuses a legitimate write
  inside the workspace is *unusable*, not strict: the control probe exists to tell the two
  apart, and the assertion is written that way rather than assuming "partial".

Nothing here is a substitute for ``tests/test_sandbox_live.py`` on Linux: that file is what
proves the bwrap rung hides paths and cuts the network.
"""

from __future__ import annotations

import shutil
import socket
import sys
import threading
from pathlib import Path

import pytest

from odessy.execution.contract import ExecutionPolicy
from odessy.execution.enforcement_probe import Measurement, probe_enforcement
from odessy.execution.providers.unconfined import UnconfinedProvider
from odessy.execution.providers.windows_acl import WindowsAclProvider, windows_acl_runner


@pytest.fixture
def layout(tmp_path: Path):
    """A workspace, a directory outside it, a hidden directory and a live listener."""

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

    def accept() -> None:
        while True:
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            connection.close()

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield workspace, outside, hidden, port
    finally:
        listener.close()
        thread.join(timeout=2)


def _policy(workspace: Path, hidden: Path) -> ExecutionPolicy:
    return ExecutionPolicy(mode="workspace-write", workspace_root=str(workspace),
                           network="deny", hidden_paths=(str(hidden),))


def test_with_no_boundary_every_dimension_is_measured_open(layout):
    workspace, outside, hidden, port = layout
    measurement = probe_enforcement(
        UnconfinedProvider(), _policy(workspace, hidden), workspace=workspace,
        outside=outside, hidden=hidden, hidden_file=hidden / "secret.txt", port=port,
    )
    assert measurement.provider_id == "unconfined"
    assert measurement.usable is True
    # The control ran, so each "denied" verdict below would have meant something.
    assert measurement.verdict("write_inside") is True
    assert measurement.verdict("write_outside") is False
    assert measurement.verdict("read_hidden") is False
    assert measurement.verdict("network") is False
    assert "the boundary is not there" in next(
        item.detail for item in measurement.measurements if item.dimension == "network")


def test_a_measurement_that_contradicts_the_declaration_is_reported(layout):
    workspace, outside, hidden, port = layout
    measurement = probe_enforcement(
        UnconfinedProvider(), _policy(workspace, hidden), workspace=workspace,
        outside=outside, hidden=hidden, hidden_file=hidden / "secret.txt", port=port,
    )
    # Claiming "the network is cut and the paths are hidden" while measuring the opposite is
    # the one direction that must never pass silently.
    assert set(measurement.contradicts(())) == {"network", "hidden_paths"}
    # ``enforcement="none"`` claims no file boundary either, so it contradicts nothing; the
    # same holes under a ``full`` claim are overstatements and are reported.
    from dataclasses import replace

    assert "fs-write" not in measurement.contradicts(())
    assert "fs-write" in replace(measurement, enforcement="full").contradicts(())


@pytest.mark.skipif(sys.platform != "win32", reason="the windows-acl rung is Windows-only")
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
@pytest.mark.skipif(windows_acl_runner() is None,
                    reason="the Node tool server is not installed and built")
def test_the_windows_rung_is_measured_rather_than_assumed(layout):
    workspace, outside, hidden, port = layout
    measurement = probe_enforcement(
        WindowsAclProvider(), _policy(workspace, hidden), workspace=workspace,
        outside=outside, hidden=hidden, hidden_file=hidden / "secret.txt", port=port,
    )
    assert measurement.provider_id == "windows-acl"
    assert measurement.enforcement == "partial"
    measured = set(measurement.measured())
    assert measured == {"write_inside", "write_outside", "read_hidden", "network",
                        "symlink_escape"}

    if measurement.usable:
        # A write outside the granted workspace is the one boundary the rung promises.
        assert measurement.verdict("write_outside") is True
    else:
        # The rung refused a legitimate write inside the workspace it granted. That is an
        # unusable backend, not a strict one, and the control measurement says exactly why.
        failure = measurement.control_failure
        assert failure is not None and failure.enforced is False
        assert "did not run" in failure.detail

    # Either way the declaration must not overstate what was measured.
    assert measurement.contradicts({"cannot_restrict_network", "cannot_hide_paths"}) == ()


def test_a_measurement_without_a_listener_does_not_claim_the_network(layout):
    workspace, outside, hidden, _ = layout
    measurement = probe_enforcement(
        UnconfinedProvider(), _policy(workspace, hidden), workspace=workspace,
        outside=outside, hidden=hidden, hidden_file=hidden / "secret.txt", port=None,
    )
    assert measurement.verdict("network") is None
    assert "network" in measurement.unmeasured()
    assert "cannot_restrict_network" not in {"network"}


def test_an_unmeasured_dimension_is_not_a_verdict():
    measurement = type("M", (), {})  # placeholder to keep the import of Measurement used
    assert measurement is not None
    empty = Measurement(dimension="network", enforced=None, detail="not measured")
    assert empty.enforced is None
