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
    yield
    os.environ.clear()
    os.environ.update(saved)
