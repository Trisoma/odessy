"""Prompt 09: the guarded tool pipeline a Harness turn reaches.

Three things are asserted here, all at the boundary that matters:

* the tool server's confinement comes from the **session**, not from the call, so a model
  cannot widen it and a call that tries is refused rather than quietly ignored;
* the **authority policy runs inside the server** on this path, because there is no agent loop
  in front of a Harness-initiated call to run it;
* the runtime admits exactly the tools of its own MCP server, and no other tool title.

Whether a real operating system confines the command is measured elsewhere
(``tests/test_enforcement_probe.py``, ``verify_enforcement.py``); these tests prove the wiring
that decides *what* reaches the provider.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from odessy.authority.policy import OdessyPolicy
from odessy.config.sandbox import SandboxSettings
from odessy.execution.contract import (
    ConfineResult,
    ExecutionPolicy,
    Limitation,
    Requirement,
)
from odessy.execution.providers.base import ConfiningProvider
from odessy.execution.registry import ProviderRegistry
from odessy.runtime_backends import RuntimeCapabilityUnavailable
from odessy.runtime_backends.harness import HarnessConfig, HarnessRuntime
from odessy.runtime_backends.harness.profile import materialize_narrow_patch
from odessy.runtime_backends.harness.sandbox import explicit_plan
from odessy.tools.mcp_server import SessionPolicy, build_odessy_mcp_server
from odessy.tools.stdio_server import authorize, scrub_environment, session_settings


class RecordingProvider(ConfiningProvider):
    """A confining backend that records the policy it was asked to enforce."""

    provider_id = "recording"

    def __init__(self) -> None:
        self.policies: list[ExecutionPolicy] = []

    def usable(self) -> tuple[bool, str]:
        return True, "recording backend"

    def limitations(self, policy: ExecutionPolicy) -> frozenset[Limitation]:
        return frozenset({"cannot_restrict_reads"})

    def wrap(self, argv, policy):  # noqa: ANN001 - protocol shape
        self.policies.append(policy)
        return list(argv)


def _server(tmp_path: Path, *, mode: str = "read-only", network: str = "allow",
            hidden: tuple[str, ...] = (), deny: str | None = None,
            workspace: Path | None = None):
    provider = RecordingProvider()
    server = build_odessy_mcp_server(
        workspace_root=workspace or tmp_path,
        registry=ProviderRegistry([provider]),
        session_policy=SessionPolicy(mode=mode, network=network, hidden_paths=hidden),
        authorize=(lambda name, arguments: deny),
    )
    return server, provider


def _call(server, name: str, arguments: dict) -> dict:
    result = asyncio.run(server.call_tool(name, arguments))
    # ``call_tool`` returns the MCP content list; the tool's dict is serialised into it.
    return result
# -- the session decides the confinement -------------------------------------

def test_the_session_mode_reaches_the_provider_not_the_call(tmp_path):
    server, provider = _server(tmp_path, mode="read-only")
    _call(server, "terminal.run", {"command": "echo hi"})
    assert [policy.mode for policy in provider.policies] == ["read-only"]


def test_a_call_that_tries_to_set_the_confinement_is_refused(tmp_path):
    server, provider = _server(tmp_path, mode="read-only")
    text = str(_call(server, "terminal.run",
                     {"command": "echo hi", "mode": "danger-full-access"}))
    assert "fixed by the session" in text
    # Nothing reached the backend: the refusal happened before any confinement decision.
    assert provider.policies == []


def test_network_and_hidden_paths_come_from_the_session(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    # A hidden folder inside the workspace is not a hidden folder: the bridge drops it, which
    # is why this one is a sibling.
    hidden = str(secrets)
    server, provider = _server(tmp_path, mode="workspace-write", network="deny",
                               hidden=(hidden,), workspace=workspace)
    _call(server, "terminal.run", {"command": "echo hi"})
    policy = provider.policies[0]
    assert policy.network == "deny"
    assert policy.hidden_paths == (hidden,)


# -- authority runs inside the server ----------------------------------------

def test_an_allowed_operation_runs(tmp_path):
    settings = SandboxSettings(hidden_paths=(), extra_writable=())
    policy = OdessyPolicy(tmp_path, sandbox=settings)
    assert authorize(policy, "terminal.run", {"command": "echo hi", "cwd": ".", "timeout": 10}) is None


def test_an_operation_needing_consent_is_refused_because_this_transport_cannot_ask(tmp_path):
    settings = SandboxSettings(hidden_paths=(), extra_writable=())
    policy = OdessyPolicy(tmp_path, sandbox=settings)
    reason = authorize(policy, "terminal.run",
                       {"command": "Remove-Item important.txt", "cwd": ".", "timeout": 10})
    assert reason is not None and "confirmation" in reason


def test_a_denied_operation_is_refused_with_its_rule(tmp_path):
    marker = tmp_path / "secret.txt"
    marker.write_text("x", encoding="utf-8")
    settings = SandboxSettings(hidden_paths=(), extra_writable=())
    policy = OdessyPolicy(tmp_path, sandbox=settings)
    reason = authorize(policy, "filesystem.read", {"path": str(Path.home() / ".ssh" / "id_rsa")})
    assert reason is not None and "policy" in reason


def test_the_server_refuses_before_reaching_the_provider_when_authority_says_no(tmp_path):
    server, provider = _server(tmp_path, deny="refused by policy (test)")
    text = str(_call(server, "terminal.run", {"command": "echo hi"}))
    assert "refused by policy" in text
    assert provider.policies == []


# -- environment hygiene -----------------------------------------------------

def test_the_tool_server_drops_inherited_secrets():
    environ = {
        "PATH": "p", "SystemRoot": "C:/Windows",
        "ODESSY_BRIDGE_TOKEN": "abc", "ODESSY_BRIDGE_PORT": "1",
        "DEEPSEEK_API_KEY": "k", "ODESSY_HARNESS_PROVIDER_KEY": "k2",
    }
    dropped = scrub_environment(environ)
    assert set(dropped) >= {"ODESSY_BRIDGE_TOKEN", "ODESSY_BRIDGE_PORT",
                            "DEEPSEEK_API_KEY", "ODESSY_HARNESS_PROVIDER_KEY"}
    assert "PATH" in environ and "SystemRoot" in environ
    assert not any(name in environ for name in dropped)


def test_the_session_settings_carry_the_plan_values():
    settings = session_settings(network="deny", hidden_paths=("C:/secrets",))
    assert settings.network_for("workspace-write") == "deny"
    assert settings.hidden_paths == ("C:/secrets",)


# -- the runtime admits only its own tool server -----------------------------

class _Session:
    id = "sess-1"
    audit = None
    stop_epoch = 0

    def model_profile(self):
        return {"name": "custom", "adapter": "cloud", "provider": "custom",
                "model": "m", "endpoint": "http://127.0.0.1:1/v1"}


def _runtime(tmp_path: Path, *, tools: bool) -> HarnessRuntime:
    home = tmp_path / "home"
    config = HarnessConfig(
        command=(sys.executable,), home=home, workspace=tmp_path,
        narrow_patch=materialize_narrow_patch(home),
        sandbox=explicit_plan(tmp_path, mode="workspace-write"), tools_enabled=tools,
        # A resolvable route, so construction reaches the tool wiring rather than failing on
        # an unconfigured provider. The value is never used: no turn is run here.
        source_env={"ODESSY_CLOUD_API_KEY": "not-a-real-key"},
    )
    return HarnessRuntime(_Session(), config=config)


def _tool_call(title: str, call_id: str = "call-1") -> list[dict]:
    return [{"method": "session/update", "params": {
        "sessionId": "acp-1",
        "update": {"sessionUpdate": "tool_call", "toolCallId": call_id, "title": title,
                   "status": "in_progress"}}}]


def test_the_runtimes_own_mcp_tool_is_admitted(tmp_path):
    runtime = _runtime(tmp_path, tools=True)
    runtime._refuse_unimplemented(_tool_call("mcp__odessy__terminal_run"), 0)
    assert "call-1" in runtime._allowed_calls


def test_a_built_in_tool_row_is_still_refused_with_tools_enabled(tmp_path):
    runtime = _runtime(tmp_path, tools=True)
    with pytest.raises(RuntimeCapabilityUnavailable):
        runtime._refuse_unimplemented(_tool_call("tool-pwsh"), 0)


def test_another_servers_mcp_tool_is_refused(tmp_path):
    runtime = _runtime(tmp_path, tools=True)
    with pytest.raises(RuntimeCapabilityUnavailable):
        runtime._refuse_unimplemented(_tool_call("mcp__other__terminal_run"), 0)


def test_no_mcp_tool_is_admitted_when_tools_are_disabled(tmp_path):
    runtime = _runtime(tmp_path, tools=False)
    assert runtime._config.mcp_servers() == []
    with pytest.raises(RuntimeCapabilityUnavailable):
        runtime._refuse_unimplemented(_tool_call("mcp__odessy__terminal_run"), 0)


def test_the_tool_server_entry_is_built_from_the_plan(tmp_path):
    runtime = _runtime(tmp_path, tools=True)
    entry = runtime._config.mcp_servers()[0]
    assert entry["name"] == "odessy"
    args = list(entry["args"])
    assert "odessy.tools.stdio_server" in args
    assert args[args.index("--mode") + 1] == runtime.sandbox.mode
    assert args[args.index("--workspace") + 1] == runtime.sandbox.workspace_root
    assert entry["env"] == []
