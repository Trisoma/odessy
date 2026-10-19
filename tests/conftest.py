from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session", autouse=True)
def _warm_windows_shell() -> None:
    """Pay the first Windows PowerShell start once, outside any test's budget.

    ``CommandBridge`` runs commands through ``powershell.exe``. On a fresh Windows
    runner the first start re-analyzes every installed module, which
    ``command_bridge`` documents as about 25 s. A test that gives a shell command a
    10 s budget is then racing that analysis instead of testing its own subject, and
    it fails on whichever job happens to start PowerShell first. Warming it here
    makes that cost one per session rather than a hidden per-test variable.

    Best effort: a host with no working shell should fail in the tests that need one,
    with a clearer message than a collection error here.
    """

    if os.name != "nt":
        return
    shell = shutil.which("powershell")
    if shell is None:
        return
    from odessy.command_bridge import build_sanitized_environment

    try:
        subprocess.run(
            [
                shell, "-NoProfile", "-NonInteractive", "-Command",
                '$ErrorActionPreference = "Stop"; '
                'Get-Location; Get-ChildItem; Write-Output ready; exit 0',
            ],
            # Warm the same module environment that real executor children see.
            env=build_sanitized_environment(os.environ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


@pytest.fixture(autouse=True)
def _isolate_config_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """Keep the developer's ``config/odessy.env`` out of the test run.

    ``load_config_env`` is called by ``VoiceConfig.from_env`` and the benchmark CLI,
    and writes the file's values into ``os.environ`` with ``setdefault``. Without
    this fixture a test's outcome depends on the local config file, and values it
    loads leak into every later test. Tests that need a config file still point
    ``ODESSY_CONFIG_FILE`` at their own.
    """

    saved = dict(os.environ)
    monkeypatch.setenv(
        "ODESSY_CONFIG_FILE",
        str(tmp_path_factory.mktemp("config") / "missing.env"),
    )
    # Most of the suite describes the two-machine deployment (laptop routing, the full
    # tool list). Pin it, so a test never depends on whether the developer's shell has
    # a tool server URL set; single-machine tests set "single" themselves.
    monkeypatch.setenv("ODESSY_DEPLOYMENT", "split")
    for name in ("ODESSY_LOCAL_OS", "ODESSY_LAPTOP_OS", "ODESSY_TOOL_SERVER_URL", "ODESSY_MCP_SERVER_URLS"):
        monkeypatch.delenv(name, raising=False)
    yield
    os.environ.clear()
    os.environ.update(saved)
