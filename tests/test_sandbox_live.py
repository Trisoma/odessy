"""Live bwrap checks: the boundary really holds on a Linux host.

Skipped unless bwrap is usable here. CI's ``sandbox-live`` job installs bubblewrap,
allows unprivileged user namespaces and sets ``ODESSY_SANDBOX_LIVE=1``, which turns a
skip into a failure, so the job cannot pass by not running. Run the same file on the
Jetson before a release::

    ODESSY_SANDBOX_LIVE=1 python -m pytest -q tests/test_sandbox_live.py
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

from odessy.authority.command_bridge import CommandBridge
from odessy.execution.providers import BwrapProvider
from odessy.execution.registry import ProviderRegistry

LIVE = os.environ.get("ODESSY_SANDBOX_LIVE") == "1"


@pytest.fixture(scope="module")
def bwrap() -> BwrapProvider:
    provider = BwrapProvider()
    usable, reason = provider.usable() if sys.platform.startswith("linux") else (False, "not Linux")
    if not usable:
        if LIVE:
            pytest.fail(f"ODESSY_SANDBOX_LIVE=1 but bwrap is not usable: {reason}")
        pytest.skip(f"bwrap is not usable here: {reason}")
    return provider


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture
def outside() -> Path:
    """A folder in the home directory: outside the workspace and outside /tmp, which
    ``workspace-write`` replaces with a private tmpfs."""

    folder = Path.home() / f".odessy-live-{uuid.uuid4().hex[:8]}"
    folder.mkdir()
    yield folder
    for child in sorted(folder.rglob("*"), reverse=True):
        child.unlink() if child.is_file() else child.rmdir()
    folder.rmdir()


def run(bwrap: BwrapProvider, workspace: Path, command: str, mode: str = "read-only", **shape):
    bridge = CommandBridge(workspace, registry=ProviderRegistry([bwrap]))
    return bridge.run(command=command, timeout=30, mode=mode, isolation="required", **shape)


def test_read_only_cannot_write_the_workspace(bwrap, workspace):
    result = run(bwrap, workspace, "touch created")
    assert result["exit_code"] != 0 and not (workspace / "created").exists()
    assert result["sandbox"]["provider_id"] == "bwrap" and result["sandbox"]["enforcement"] == "full"


def test_workspace_write_writes_the_workspace_but_nothing_outside(bwrap, workspace, outside):
    inside = run(bwrap, workspace, "echo ok > created", mode="workspace-write")
    assert inside["exit_code"] == 0 and (workspace / "created").read_text().strip() == "ok"
    escaped = run(bwrap, workspace, f"echo no > {outside}/escaped", mode="workspace-write")
    assert escaped["exit_code"] != 0 and not (outside / "escaped").exists()


def test_an_extra_writable_folder_is_writable(bwrap, workspace, outside):
    result = run(bwrap, workspace, f"echo ok > {outside}/cache", mode="workspace-write", extra_writable=[str(outside)])
    assert result["exit_code"] == 0 and (outside / "cache").exists()


CONNECT = f"{sys.executable} -c \"import socket; socket.create_connection(('1.1.1.1', 53), 5)\""


def test_network_is_cut_when_denied_and_open_otherwise(bwrap, workspace):
    open_ = run(bwrap, workspace, CONNECT, network="allow")
    assert open_["exit_code"] == 0, open_["stderr"]  # the runner has a network: deny below is real
    assert open_["sandbox"]["network"] == "open"
    cut = run(bwrap, workspace, CONNECT, network="deny")
    assert cut["exit_code"] != 0
    assert cut["sandbox"]["network"] == "denied"


def test_hidden_paths_cannot_be_read(bwrap, workspace, outside):
    secrets = outside / "secrets"
    secrets.mkdir()
    (secrets / "key").write_text("top secret\n")
    (outside / "token").write_text("token value\n")
    folder = run(bwrap, workspace, f"cat {secrets}/key", hidden_paths=[str(secrets)])
    assert "top secret" not in folder["stdout"] and folder["exit_code"] != 0
    file = run(bwrap, workspace, f"cat {outside}/token", hidden_paths=[str(outside / "token")])
    assert "token value" not in file["stdout"]
    assert folder["sandbox"]["hidden_paths"] == 1
    visible = run(bwrap, workspace, f"cat {secrets}/key")
    assert "top secret" in visible["stdout"]  # and readable without hiding: the hiding is what stops it


def test_a_file_size_limit_stops_a_large_write(bwrap, workspace):
    result = run(bwrap, workspace, "head -c 3000000 /dev/zero > big", mode="workspace-write", limits={"file_size_mb": 1})
    assert result["exit_code"] != 0
    assert (workspace / "big").stat().st_size <= 1024 * 1024
    assert result["sandbox"]["limits"]["applied"] == {"file_size_mb": 1}
