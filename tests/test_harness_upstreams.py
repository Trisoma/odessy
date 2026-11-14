"""Prompt 09a: which upstream tool servers a Harness session attaches, and why not.

These are the *decision* tests: whether an upstream may be attached at all, what argv and
environment its gateway receives, and what is refused. What a call may then do is
``tests/test_upstream_gateway.py``; what the whole path does against the pinned runtime is the
smoke in ``experiments/restructure/harness_compatibility/smoke_p09a.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from odessy.runtime_backends.contracts import RuntimeUnavailable
from odessy.runtime_backends.harness import HarnessConfig
from odessy.runtime_backends.harness.config import from_environment
from odessy.runtime_backends.harness.profile import NARROW_PATCH_NAME, NARROW_PATCH_TEXT
from odessy.runtime_backends.harness.sandbox import explicit_plan
from odessy.runtime_backends.harness.upstreams import (
    EXTERNAL_PREFIX,
    NODE_SERVER_NAME,
    NodeTools,
    activation,
)
from odessy.tools.external_tools import ServerConfig

_WINDOWS_GAPS = ("cannot_restrict_network", "cannot_hide_paths")


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    return workspace


def _external(name: str = "fixture", *, env: tuple[str, ...] = (),
              headers: dict[str, str] | None = None) -> ServerConfig:
    return ServerConfig(
        name=name, transport="stdio", command=(sys.executable, "-c", "pass"),
        env=env, headers=headers or {}, trust="untrusted",
    )


def _config(tmp_path: Path, *, plan=None, **overrides) -> HarnessConfig:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    workspace = _workspace(tmp_path)
    overlay = home / NARROW_PATCH_NAME
    overlay.write_text(NARROW_PATCH_TEXT, encoding="utf-8")
    settings = dict(
        command=(sys.executable,),
        home=home,
        workspace=workspace,
        narrow_patch=overlay,
        sandbox=plan or explicit_plan(workspace, mode="workspace-write", network="allow"),
        tools_enabled=True,
        verify_artifact=False,
    )
    settings.update(overrides)
    return HarnessConfig(**settings)


def _node(tmp_path: Path) -> NodeTools:
    entry = tmp_path / "server.js"
    entry.write_text("// built entry\n", encoding="utf-8")
    return NodeTools(executable=sys.executable, entry=entry)


# -- the confinement rule -------------------------------------------------------

def test_a_third_party_server_attaches_under_preferred_and_names_its_gaps(tmp_path):
    """``preferred`` accepts a weaker boundary, so the gap is named rather than hidden."""

    plan = explicit_plan(_workspace(tmp_path), mode="workspace-write", network="deny",
                         isolation="preferred", limitations=_WINDOWS_GAPS)
    decision = activation("external", plan)
    assert decision["allowed"] is True
    assert decision["unenforced"] == ["network"]
    assert decision["process_confinement"] == "none"
    assert "cannot enforce network" in decision["note"]
    assert "policy and consent" in decision["note"]


def test_a_third_party_server_is_allowed_when_nothing_extra_is_asked(tmp_path):
    plan = explicit_plan(_workspace(tmp_path), mode="workspace-write", network="allow",
                         isolation="preferred", limitations=_WINDOWS_GAPS)
    decision = activation("external", plan)
    assert decision["allowed"] is True
    # Never claimed: the process itself is not in an OS sandbox.
    assert decision["process_confinement"] == "none"
    assert decision["unenforced"] == []


def test_the_node_server_reports_gaps_but_attaches_under_preferred_isolation(tmp_path):
    """Odessy's own tools keep parity with the command path: report, do not pretend."""

    plan = explicit_plan(_workspace(tmp_path), mode="workspace-write", network="deny",
                         isolation="preferred", limitations=_WINDOWS_GAPS)
    decision = activation("node", plan)
    assert decision["allowed"] is True
    assert decision["unenforced"] == ["network"]
    assert "cannot enforce network" in decision["note"]


def test_required_isolation_refuses_the_session_before_any_upstream_is_considered(tmp_path):
    """On this host a ``required`` boundary is refused outright — upstreams never start.

    That is the first line of defence. ``activation('node', …)`` keeps the same rule for a
    host whose plan *is* launchable but whose server processes still cannot be confined
    (see :func:`odessy.runtime_backends.harness.upstreams.activation`), so the gateway is not
    the only place that could fail open.
    """

    from odessy.runtime_backends.harness.sandbox import SandboxRequirementRefused

    with pytest.raises(SandboxRequirementRefused):
        explicit_plan(_workspace(tmp_path), mode="workspace-write", network="deny",
                      isolation="required", limitations=_WINDOWS_GAPS)


def test_no_dimension_is_requested_for_danger_full_access(tmp_path):
    plan = explicit_plan(_workspace(tmp_path), mode="danger-full-access", network="deny",
                         isolation="preferred", limitations=_WINDOWS_GAPS)
    assert activation("external", plan)["allowed"] is True


# -- what the session attaches --------------------------------------------------

def test_the_attachment_lists_each_accepted_server_and_records_each_refusal(tmp_path):
    workspace = _workspace(tmp_path)
    plan = explicit_plan(workspace, mode="workspace-write", network="deny",
                         isolation="preferred", limitations=_WINDOWS_GAPS)
    config = _config(tmp_path, plan=plan, node_tools=_node(tmp_path),
                     external_tools=(_external("fixture"),),
                     servers_file=tmp_path / "servers.toml")

    servers = config.mcp_servers()
    names = [entry["name"] for entry in servers]
    # Odessy's own server, the Node one and the third-party one are all attached; the gaps
    # this host cannot hold the upstream processes to are recorded, not hidden.
    assert names == ["odessy", NODE_SERVER_NAME, f"{EXTERNAL_PREFIX}fixture"]
    node = servers[1]
    assert node["args"][:2] == ["-m", "odessy.tools.upstream_gateway"]
    assert "--node-exe" in node["args"] and "--entry" in node["args"]

    record = config.describe()["upstreams"]
    assert record["node"]["allowed"] is True
    assert record["node"]["unenforced"] == ["network"]
    assert record["external"]["fixture"]["allowed"] is True
    assert record["external"]["fixture"]["label"] == f"{EXTERNAL_PREFIX}fixture"


def test_an_external_gateway_gets_only_its_declared_environment(tmp_path):
    """A credential reaches the gateway only when that server's config asked for it."""

    workspace = _workspace(tmp_path)
    plan = explicit_plan(workspace, mode="workspace-write", network="allow")
    source = {
        "PATH": os.environ.get("PATH", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "FIXTURE_TOKEN": "s3cret-value",
        "UNRELATED_SECRET": "must-not-travel",
    }
    config = _config(
        tmp_path, plan=plan, source_env=source,
        external_tools=(_external("fixture", env=("FIXTURE_TOKEN",),
                                  headers={"Authorization": "FIXTURE_TOKEN"}),),
        servers_file=tmp_path / "servers.toml")

    entries = [entry for entry in config.mcp_servers() if entry["name"].endswith("fixture")]
    assert len(entries) == 1
    env = {item["name"]: item["value"] for item in entries[0]["env"]}
    assert env["FIXTURE_TOKEN"] == "s3cret-value"
    assert "UNRELATED_SECRET" not in env
    # Names only in diagnostics, never the value.
    assert "s3cret-value" not in str(config.describe())


def test_no_upstream_is_attached_when_the_tool_surface_is_off(tmp_path):
    config = _config(tmp_path, tools_enabled=False, node_tools=_node(tmp_path))
    assert config.mcp_servers() == []


# -- refusals at session creation ----------------------------------------------

def test_an_upstream_without_the_tool_surface_is_refused(tmp_path):
    config = _config(tmp_path, tools_enabled=False, node_tools=_node(tmp_path))
    with pytest.raises(RuntimeUnavailable) as refused:
        config.validate()
    assert "requires the tool surface" in str(refused.value)


def test_a_node_server_that_was_never_built_is_refused(tmp_path):
    node = NodeTools(executable=sys.executable, entry=tmp_path / "missing" / "server.js")
    config = _config(tmp_path, node_tools=node)
    with pytest.raises(RuntimeUnavailable) as refused:
        config.validate()
    assert "not built" in str(refused.value)


def test_a_node_executable_that_does_not_exist_is_refused(tmp_path):
    node = NodeTools(executable=str(tmp_path / "nope" / "node.exe"),
                     entry=_node(tmp_path).entry)
    config = _config(tmp_path, node_tools=node)
    with pytest.raises(RuntimeUnavailable) as refused:
        config.validate()
    assert "executable" in str(refused.value)


def test_an_unknown_external_server_name_is_refused(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    servers = tmp_path / "servers.toml"
    servers.write_text(
        "[servers.known]\n"
        'transport = "stdio"\n'
        f"command = ['{Path(sys.executable).as_posix()}', '-c', 'pass']\n",
        encoding="utf-8",
    )
    overlay = tmp_path / NARROW_PATCH_NAME
    overlay.write_text(NARROW_PATCH_TEXT, encoding="utf-8")
    environ = {
        "ODESSY_HARNESS_EXE": sys.executable,
        "ODESSY_HARNESS_HOME": str(tmp_path / "home"),
        "ODESSY_HARNESS_WORKSPACE": str(workspace),
        "ODESSY_HARNESS_TOOLS": "on",
        "ODESSY_HARNESS_EXTERNAL_TOOLS": "known,typo",
        "ODESSY_SERVERS_FILE": str(servers),
        "PATH": os.environ.get("PATH", ""),
    }
    with pytest.raises(RuntimeUnavailable) as refused:
        from_environment(environ, workspace=workspace)
    assert "not configured" in str(refused.value)
    assert "typo" in str(refused.value)


def test_a_malformed_servers_file_is_refused_rather_than_ignored(tmp_path):
    workspace = _workspace(tmp_path)
    servers = tmp_path / "servers.toml"
    servers.write_text("[servers.broken]\ntransport = 12\n", encoding="utf-8")
    environ = {
        "ODESSY_HARNESS_EXE": sys.executable,
        "ODESSY_HARNESS_HOME": str(tmp_path / "home"),
        "ODESSY_HARNESS_WORKSPACE": str(workspace),
        "ODESSY_HARNESS_TOOLS": "on",
        "ODESSY_HARNESS_EXTERNAL_TOOLS": "broken",
        "ODESSY_SERVERS_FILE": str(servers),
        "PATH": os.environ.get("PATH", ""),
    }
    with pytest.raises(RuntimeUnavailable) as refused:
        from_environment(environ, workspace=workspace)
    assert "cannot be read" in str(refused.value)

@pytest.mark.parametrize('kind', ['node', 'external'])
@pytest.mark.parametrize('mode', ['read-only', 'workspace-write'])
def test_required_isolation_refuses_an_unconfined_upstream_even_without_extra_dimensions(tmp_path, kind, mode):
    """A capable host provider is not confinement of a separately spawned MCP server."""
    plan = explicit_plan(_workspace(tmp_path), mode=mode, network='allow', hidden_paths=(),
                         isolation='required', provider='capable-host', enforcement='full',
                         limitations=(), boundary='odessy')
    assert plan.launchable
    decision = activation(kind, plan)
    assert decision['allowed'] is False
    assert decision['process_confinement'] == 'none'
    assert 'mode' in decision['unenforced']
    assert 'server process' in decision['reason']


def test_required_isolation_refuses_session_creation_before_starting_an_upstream(tmp_path):
    """Activation refusal must be a launch error, not a silently omitted requested server."""
    plan = explicit_plan(_workspace(tmp_path), mode='workspace-write', network='allow',
                         isolation='required', provider='capable-host', enforcement='full',
                         limitations=(), boundary='odessy')
    config = _config(tmp_path, plan=plan, node_tools=_node(tmp_path))
    with pytest.raises(RuntimeUnavailable, match='upstream.*isolation|isolation.*upstream'):
        config.validate()
