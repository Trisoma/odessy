"""The paired-host adapter: configuration rules, transport command and identity."""

import pathlib

import pytest

from odessy.config.deployment import (
    DeploymentConfigError,
    command_tool_for_laptop,
    deployment_mode,
    environment_prompt,
    model_tool_names,
)
from odessy.tools.paired import (
    LINK_BIN_ENV,
    PAIRED_HOST_ENV,
    PairedConfigError,
    link_binary,
    paired_host,
    probe,
)


@pytest.fixture()
def fake_binary(tmp_path):
    path = tmp_path / ("odessy-link.exe" if __import__("os").name == "nt" else "odessy-link")
    path.write_text("stub", encoding="ascii")
    return path


def test_no_configuration_means_no_paired_host():
    assert paired_host({}) is None
    assert deployment_mode({}) == "single"


def test_a_paired_host_selects_split_execution(fake_binary):
    env = {PAIRED_HOST_ENV: "jetson", LINK_BIN_ENV: str(fake_binary)}
    host = paired_host(env)
    assert host is not None
    assert host.alias == "jetson"
    assert deployment_mode(env) == "split"


def test_a_bad_alias_is_refused(fake_binary):
    env = {PAIRED_HOST_ENV: "not an alias!", LINK_BIN_ENV: str(fake_binary)}
    with pytest.raises(PairedConfigError, match="ASCII letters"):
        paired_host(env)
    with pytest.raises(DeploymentConfigError):
        deployment_mode(env)


def test_pairing_contradicts_single_mode_and_remote_urls(fake_binary):
    base = {PAIRED_HOST_ENV: "jetson", LINK_BIN_ENV: str(fake_binary)}
    with pytest.raises(DeploymentConfigError, match="contradicts"):
        deployment_mode({**base, "ODESSY_DEPLOYMENT": "single"})
    for name in ("ODESSY_MCP_SERVER_URLS", "ODESSY_TOOL_SERVER_URL"):
        with pytest.raises(DeploymentConfigError, match="cannot be combined"):
            deployment_mode({**base, name: "http://laptop:8765/mcp"})
    # An explicit split declaration is consistent, not contradictory.
    assert deployment_mode({**base, "ODESSY_DEPLOYMENT": "split"}) == "split"


def test_a_binary_without_an_alias_is_a_configuration_error(fake_binary):
    with pytest.raises(PairedConfigError, match=LINK_BIN_ENV):
        paired_host({LINK_BIN_ENV: str(fake_binary)})


def test_a_missing_binary_is_named_precisely(tmp_path):
    with pytest.raises(PairedConfigError, match="not a file"):
        link_binary({LINK_BIN_ENV: str(tmp_path / "absent")})


def test_the_transport_command_is_a_connect_for_the_enrolled_alias(fake_binary):
    host = paired_host({PAIRED_HOST_ENV: "jetson", LINK_BIN_ENV: str(fake_binary)})
    config = host.server_config()
    assert config.name == "jetson"
    assert config.transport == "stdio"
    assert config.command == (str(fake_binary), "connect", "--peer", "jetson")
    # Only the transport's own settings are forwarded, never the ambient environment.
    assert config.env == ("ODESSY_LINK_STATE_DIR", "ODESSY_LINK_NODE")
    # The enrolled alias travels with the target so approvals name the peer.
    assert config.target_metadata["paired_alias"] == "jetson"
    assert config.target_metadata["transport"] == "odessy-link"


def test_the_paired_namespace_replaces_the_laptop_namespace(fake_binary, monkeypatch):
    for name, value in ((PAIRED_HOST_ENV, "jetson"), (LINK_BIN_ENV, str(fake_binary))):
        monkeypatch.setenv(name, value)
    # The served Node server names its command tool, so the alias never enters the
    # tool name: `jetson.laptop.terminal.run` was the bug this pins down.
    assert command_tool_for_laptop("split") == "laptop.terminal.run"
    assert "laptop.terminal.run" not in model_tool_names("single")
    assert "laptop.terminal.run" in model_tool_names("split")
    prompt = environment_prompt(
        ["terminal.run", "laptop.terminal.run"], env={PAIRED_HOST_ENV: "jetson"}
    )
    assert "laptop.terminal.run runs commands on the user's laptop" in prompt


def test_probe_reports_a_missing_binary_rather_than_raising(tmp_path):
    host = paired_host(
        {PAIRED_HOST_ENV: "jetson", LINK_BIN_ENV: str(_write_stub(tmp_path))}
    )
    result = probe(host)
    assert result["ok"] is True or "detail" in result
    assert isinstance(result["alias"], str)


def _write_stub(tmp_path: pathlib.Path) -> pathlib.Path:
    stub = tmp_path / "odessy-link"
    stub.write_text("not executable", encoding="ascii")
    return stub


def test_probe_marks_an_unknown_alias_as_not_enrolled(tmp_path):
    stub = tmp_path / "odessy-link"
    stub.write_text("stub", encoding="ascii")
    host = paired_host({PAIRED_HOST_ENV: "jetson", LINK_BIN_ENV: str(stub)})
    result = probe(host, timeout=5)
    assert result["ok"] is False
    assert "detail" in result


def test_approval_metadata_names_the_enrolled_certificate_and_revision(fake_binary, monkeypatch):
    """An approval must name the certificate that will be trusted, not just an alias."""

    fingerprint = "ab" * 32
    recorded = {}

    def prober(host):
        recorded["alias"] = host.alias
        return {
            "ok": True,
            "enrolled_peers": 1,
            "configured_alias_present": True,
            "fingerprint_sha256": fingerprint,
            "grant_revision": 7,
            "capability_mcp": True,
            "workspace": r"C:\work",
        }

    monkeypatch.setenv(PAIRED_HOST_ENV, "jetson")
    monkeypatch.setenv(LINK_BIN_ENV, str(fake_binary))
    host = paired_host(prober=prober)
    assert recorded["alias"] == "jetson"
    metadata = host.server_config().target_metadata
    assert metadata["paired_alias"] == "jetson"
    assert metadata["paired_fingerprint_sha256"] == fingerprint
    assert metadata["paired_grant_revision"] == "7"


def _paired_cli(monkeypatch, listing):
    """Point the composition root at a fake paired host with a scripted lister."""

    import asyncio

    import odessy.cli as cli
    from odessy.tools.paired import PairedHost

    host = PairedHost(alias="jetson", binary=pathlib.Path("odessy-link"))
    monkeypatch.setattr(cli, "_paired_host", lambda: host)
    monkeypatch.setattr(cli, "deployment_mode", lambda *args, **kwargs: "split")

    class FakeServer:
        def __init__(self, config, sessions):
            self.config = config

        async def list_tools(self):
            return listing()

    # PairedServer builds its session-backed server on demand, from this module.
    monkeypatch.setattr("odessy.tools.external_tools.NamespacedServer", FakeServer)

    async def collect(tmp_path):
        async with cli._builtin_tool_servers(tmp_path) as servers:
            return servers

    return lambda tmp_path: asyncio.run(collect(tmp_path))


def test_a_paired_host_is_the_only_execution_target(monkeypatch, tmp_path):
    import asyncio

    collect = _paired_cli(monkeypatch, lambda: [object()])
    servers = collect(tmp_path)
    assert len(servers) == 2, "python server plus the paired host, nothing else"
    paired = servers[1].server
    assert paired.config.command == ("odessy-link", "connect", "--peer", "jetson")
    assert paired.prefix == "", "the peer already names its tools; adding an alias doubles it"


def test_an_unreachable_paired_host_never_falls_back(monkeypatch, tmp_path):
    import pytest as _pytest

    def boom():
        raise ConnectionError("peer unreachable")

    collect = _paired_cli(monkeypatch, boom)
    with _pytest.raises(SystemExit) as raised:
        collect(tmp_path)
    message = str(raised.value)
    assert "Paired host 'jetson' is unavailable" in message
    assert "No legacy transport is used instead" in message

