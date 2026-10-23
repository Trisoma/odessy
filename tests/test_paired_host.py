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
    PairedHost,
    PairedTrustError,
    link_binary,
    paired_host,
    probe,
)


@pytest.fixture()
def fake_binary(tmp_path):
    path = tmp_path / ("odessy-link.exe" if __import__("os").name == "nt" else "odessy-link")
    path.write_text("stub", encoding="ascii")
    return path


def enrolled(**overrides):
    """A probe result for an enrolled, permitted peer with a known identity."""

    return {
        "ok": True,
        "enrolled_peers": 1,
        "configured_alias_present": True,
        "fingerprint_sha256": "ab" * 32,
        "grant_revision": 7,
        "capability_mcp": True,
        "workspace": r"C:\work",
        **overrides,
    }


def stub_binary(tmp_path):
    path = tmp_path / ("odessy-link.exe" if __import__("os").name == "nt" else "odessy-link")
    path.write_text("stub", encoding="ascii")
    return path


def test_no_configuration_means_no_paired_host():
    assert paired_host({}) is None
    assert deployment_mode({}) == "single"


def test_a_paired_host_selects_split_execution(tmp_path):
    env = {PAIRED_HOST_ENV: "jetson", LINK_BIN_ENV: str(stub_binary(tmp_path))}
    host = paired_host(env, prober=lambda _host: enrolled())
    assert host is not None
    assert host.alias == "jetson"
    assert deployment_mode(env) == "split"


def test_startup_fails_closed_without_a_readable_identity(tmp_path):
    """An approval must name a certificate and a revision; neither may be omitted."""

    env = {PAIRED_HOST_ENV: "jetson", LINK_BIN_ENV: str(stub_binary(tmp_path))}
    with pytest.raises(PairedConfigError, match="cannot read local trust"):
        paired_host(env, prober=lambda _host: {"ok": False, "detail": "registry unreadable"})
    with pytest.raises(PairedConfigError, match="not enrolled"):
        paired_host(env, prober=lambda _host: enrolled(configured_alias_present=False))
    with pytest.raises(PairedConfigError, match="fingerprint and revision"):
        paired_host(env, prober=lambda _host: enrolled(fingerprint_sha256=None))


def test_a_bad_alias_is_refused(fake_binary):
    env = {PAIRED_HOST_ENV: "not an alias!", LINK_BIN_ENV: str(fake_binary)}
    with pytest.raises(PairedConfigError, match="ASCII letters"):
        paired_host(env, prober=lambda _host: enrolled())
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
    host = paired_host(
        {PAIRED_HOST_ENV: "jetson", LINK_BIN_ENV: str(fake_binary)},
        prober=lambda _host: enrolled(),
    )
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
    host = PairedHost(alias="jetson", binary=tmp_path / "absent-binary")
    result = probe(host, timeout=5)
    assert result["ok"] is False
    assert "detail" in result
    assert isinstance(result["alias"], str)


def _built_binary():
    root = pathlib.Path(__file__).resolve().parents[1]
    crate = root / "native" / "odessy-link" / "target"
    name = "odessy-link.exe" if __import__("os").name == "nt" else "odessy-link"
    for profile in ("debug", "release"):
        candidate = crate / profile / name
        if candidate.is_file():
            return candidate
    return None


def test_peers_list_json_is_machine_readable_on_a_fresh_state_directory(tmp_path):
    """Helper output must never reach stdout: `--json` has to be the only thing there.

    On Windows the state directory is locked down with `icacls`, whose progress text
    lands on stdout when the child inherits our handles. That made a healthy
    `peers list --json` unparseable, so the adapter refused to start.
    """

    binary = _built_binary()
    if binary is None:
        pytest.skip("build native/odessy-link first (cargo build)")
    import json
    import os
    import subprocess

    state = tmp_path / "state"
    env = {**os.environ, "ODESSY_LINK_STATE_DIR": str(state)}
    init = subprocess.run(
        [str(binary), "init"], capture_output=True, text=True, env=env, timeout=60
    )
    assert init.returncode == 0, init.stderr
    listing = subprocess.run(
        [str(binary), "peers", "list", "--json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert listing.returncode == 0, listing.stderr
    document = json.loads(listing.stdout)
    assert document["peers"] == []
    assert document["grant_revision"] == 0


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


def _server(tmp_path, **overrides):
    from odessy.tools.paired import PairedHost

    host = PairedHost(
        alias="jetson",
        binary=stub_binary(tmp_path),
        enrolled_fingerprint="ab" * 32,
        grant_revision=7,
    )
    state = {"current": enrolled(**overrides)}
    server = host.server()
    object.__setattr__(server, "prober", lambda _host: state["current"])
    return server, state


def test_preflight_allows_an_unchanged_local_trust_state(tmp_path):
    server, _ = _server(tmp_path)
    server.preflight()


def test_preflight_refuses_a_re_enrolled_peer(tmp_path):
    """A new certificate under the same alias must invalidate the approval."""

    server, state = _server(tmp_path)
    state["current"] = enrolled(fingerprint_sha256="cd" * 32)
    with pytest.raises(PairedTrustError, match="re-enrolled"):
        server.preflight()


def test_preflight_refuses_a_changed_permission_revision(tmp_path):
    server, state = _server(tmp_path)
    state["current"] = enrolled(grant_revision=8)
    with pytest.raises(PairedTrustError, match="permission revision changed"):
        server.preflight()


def test_preflight_refuses_a_revoked_capability(tmp_path):
    server, state = _server(tmp_path)
    state["current"] = enrolled(capability_mcp=False)
    with pytest.raises(PairedTrustError, match="no longer holds"):
        server.preflight()


def test_preflight_fails_closed_when_trust_is_unreadable(tmp_path):
    server, state = _server(tmp_path)
    state["current"] = {"ok": False, "detail": "registry unreadable"}
    with pytest.raises(PairedTrustError, match="cannot read local trust"):
        server.preflight()


def test_the_agent_rechecks_paired_trust_before_dispatch(tmp_path):
    """The dispatch path itself must call the preflight, not only a direct caller."""

    import asyncio

    from odessy.runtime.agent_loop import OdessyAgent, _BoundArguments
    from odessy.runtime.types import ToolRequest

    calls = []

    class Server:
        def __init__(self):
            self.preflight_calls = 0

        def preflight(self):
            self.preflight_calls += 1
            calls.append("preflight")

        async def call_tool(self, name, arguments, *, before_dispatch=None):
            calls.append("call")
            if before_dispatch is not None:
                before_dispatch()
            return {"ok": True}

    class Tool:
        name = "terminal.run"

    server = Server()
    registry = {"terminal.run": (Tool(), server)}
    agent = OdessyAgent.__new__(OdessyAgent)
    asyncio.run(agent._call_tool("terminal.run", {}, registry))
    assert calls[0] == "preflight", "preflight runs before the call is dispatched"
    assert server.preflight_calls == 1

    def _noop_bound():
        return _BoundArguments({"command": "ls"}, lambda: None)

    calls.clear()
    asyncio.run(agent._call_tool("terminal.run", _noop_bound(), registry))
    assert calls[0] == "preflight"
    assert server.preflight_calls == 2


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

