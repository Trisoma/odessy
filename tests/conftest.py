from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from collections.abc import Iterator

import pytest


def discard_fixture_git(root: Path) -> None:
    """Remove only metadata belonging to this test and its patch-job siblings.

    Working files stay available for failure diagnosis, but editors must not
    discover these disposable repositories after the test finishes.
    """
    root=root.resolve()
    candidates=[root,*root.parent.glob(root.name+'-odessy-*')]
    def writable_retry(function,path,error):
        os.chmod(path,stat.S_IWRITE|stat.S_IREAD);function(path)
    for candidate in candidates:
        if candidate.is_symlink():continue
        actual=candidate.resolve()
        if actual.parent!=root.parent:raise ValueError('fixture cleanup escaped its temporary directory')
        metadata=actual/'.git'
        if metadata.is_symlink():continue
        if metadata.is_dir():shutil.rmtree(metadata,onerror=writable_retry)
        elif metadata.is_file():metadata.unlink()


@pytest.fixture
def isolated_patch_repo(tmp_path):
    try:yield tmp_path
    finally:discard_fixture_git(tmp_path)


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
    from odessy.models.usage import reset_process_ledger

    # The cloud usage ledger is per process; each test starts with an empty one, and
    # without the developer's budget or price settings.
    for name in list(os.environ):
        if name.startswith(("ODESSY_CLOUD_BUDGET_", "ODESSY_CLOUD_PRICE_", "ODESSY_INTERACTION_")):
            monkeypatch.delenv(name, raising=False)
    reset_process_ledger()
    monkeypatch.setenv(
        "ODESSY_CONFIG_FILE",
        str(tmp_path_factory.mktemp("config") / "missing.env"),
    )
    # Likewise the developer's third-party servers: a test configures its own.
    monkeypatch.setenv(
        "ODESSY_SERVERS_FILE",
        str(tmp_path_factory.mktemp("servers") / "missing.toml"),
    )
    # And the audit log of third-party calls, which defaults to the home directory.
    monkeypatch.setenv(
        "ODESSY_AUDIT_FILE",
        str(tmp_path_factory.mktemp("audit") / "external-audit.jsonl"),
    )
    # And the egress log of cloud requests, likewise in the home directory.
    monkeypatch.setenv(
        "ODESSY_EGRESS_FILE",
        str(tmp_path_factory.mktemp("egress") / "egress.jsonl"),
    )
    # And the commands the developer approved in advance for their workspaces.
    monkeypatch.setenv(
        "ODESSY_COMMANDS_FILE",
        str(tmp_path_factory.mktemp("commands") / "missing.toml"),
    )
    monkeypatch.setenv("ODESSY_COLLABORATION_FILE", str(tmp_path_factory.mktemp("collaboration") / "missing.toml"))
    # Most of the suite describes the two-machine deployment (laptop routing, the full
    # tool list). Pin it, so a test never depends on whether the developer's shell has
    # a tool server URL set; single-machine tests set "single" themselves.
    monkeypatch.setenv("ODESSY_DEPLOYMENT", "split")
    for name in ("ODESSY_LOCAL_OS", "ODESSY_LAPTOP_OS", "ODESSY_TOOL_SERVER_URL", "ODESSY_MCP_SERVER_URLS", "ODESSY_VOICE_SETTLING_MS"):
        monkeypatch.delenv(name, raising=False)
    yield
    reset_process_ledger()
    os.environ.clear()
    os.environ.update(saved)
