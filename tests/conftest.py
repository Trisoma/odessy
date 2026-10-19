from __future__ import annotations

import os
from collections.abc import Iterator

import pytest


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
