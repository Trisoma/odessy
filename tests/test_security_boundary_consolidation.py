"""The shared lists behind the security boundary, and the gaps they closed."""
from __future__ import annotations

from pathlib import Path

import pytest

from odessy.authority.action_approval import build_action_approval
from odessy.authority.environment import CODING_CLI_ENV, HOOK_ENV, SANDBOX_COMMAND_ENV, inherit
from odessy.authority.policy import OdessyPolicy, collaboration_decision
from odessy.collaboration.context import ApprovalGrants
from odessy.config.protected import guarded_config_files, protected_config_paths
from odessy.runtime.types import PolicyDecision, ToolRequest
from odessy.tools.catalog import BUILTIN_TOOLS, PROCESS_TOOLS, READ_ONLY_TOOLS
from odessy.tools.tool_contracts import COLLABORATION_SCHEMAS, INTERACTION_SCHEMAS, TOOL_REQUEST_SCHEMAS


def test_every_builtin_tool_has_a_contract():
    schemas = TOOL_REQUEST_SCHEMAS | COLLABORATION_SCHEMAS | INTERACTION_SCHEMAS
    assert BUILTIN_TOOLS <= set(schemas)
    assert PROCESS_TOOLS | READ_ONLY_TOOLS <= BUILTIN_TOOLS


def test_a_moved_servers_file_is_still_protected(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    servers = workspace / "servers.toml"
    env = {"ODESSY_SERVERS_FILE": str(servers), "ODESSY_HOOKS_FILE": str(tmp_path / "h" / "hooks.toml"),
           "ODESSY_SANDBOX_FILE": str(tmp_path / "s" / "sandbox.toml")}
    assert servers in guarded_config_files(env)
    policy = OdessyPolicy(workspace_root=workspace, protected_paths=protected_config_paths(env))
    decision = policy.evaluate(ToolRequest("filesystem.write", {"path": "servers.toml", "content": "[[server]]"}))
    assert decision.action == "deny" and decision.rule == "protected_config"


def test_the_prompt_does_not_promise_a_network_cut_the_host_cannot_make(tmp_path):
    policy = OdessyPolicy(
        workspace_root=tmp_path,
        posture={"local": {"workspace-write": ("windows-acl", "partial")}},
        limitations={"local": {"workspace-write": ("cannot_restrict_network", "cannot_restrict_reads")}},
    )
    request = ToolRequest("terminal.run", {"command": "touch x"})
    decision = PolicyDecision("confirm", "may modify state", "medium", rule="command_write", network="deny")
    plan = policy.execution_plan(request, decision)
    assert plan["network"] == "deny" and "network" in plan["unenforced"]
    approval = build_action_approval(request, decision, policy, call_id="c1", execution_target="local")
    assert "cannot cut it" in approval.authoritative_prompt
    assert "network off" not in approval.authoritative_prompt


def test_a_host_that_can_cut_the_network_says_so(tmp_path):
    policy = OdessyPolicy(workspace_root=tmp_path, posture={"local": {"workspace-write": ("bwrap", "full")}},
                          limitations={"local": {"workspace-write": ()}})
    request = ToolRequest("terminal.run", {"command": "touch x"})
    decision = PolicyDecision("confirm", "may modify state", "medium", rule="command_write", network="deny")
    assert "unenforced" not in policy.execution_plan(request, decision)


def test_grants_are_one_use_named_and_expire():
    now = [0.0]
    grants = ApprovalGrants(ttl=10, clock=lambda: now[0])
    token = grants.issue("agent.delegate")
    assert not grants.redeem(token, "agent.run")  # wrong tool, and now spent
    assert not grants.redeem(token, "agent.delegate")
    token = grants.issue("agent.delegate")
    assert grants.redeem(token, "agent.delegate")
    assert not grants.redeem(token, "agent.delegate")
    late = grants.issue("agent.run")
    now[0] = 11
    assert not grants.redeem(late, "agent.run")
    assert not grants.redeem("", "agent.run") and not grants.redeem(None, "agent.run")


class _Profile:
    def __init__(self, capabilities=("delegate",), modes=("advice",)):
        self.capabilities, self.allowed_modes = capabilities, modes


def test_direct_and_proposed_delegations_share_one_rule():
    participants = {"coder": _Profile()}
    ok = collaboration_decision(ToolRequest("agent.delegate", {"participant": "coder", "task": "t"}), participants)
    assert ok.action == "confirm" and ok.rule == "collaboration_delegate"
    patch = collaboration_decision(ToolRequest("agent.delegate", {"participant": "coder", "task": "t", "mode": "patch"}),
                                   participants)
    assert patch.action == "deny" and patch.rule == "collaboration_mode"
    unknown = collaboration_decision(ToolRequest("agent.delegate", {"participant": "nobody", "task": "t"}), participants)
    assert unknown.action == "deny"
    handoff = collaboration_decision(ToolRequest("agent.run_continue", {}), participants)
    assert handoff.action == "confirm" and handoff.risk == "high"


def test_environment_allowlists_match_case_insensitively():
    source = {"Path": "p", "OPENAI_API_KEY": "k", "UserProfile": "u", "TERM": "x"}
    assert inherit(source, SANDBOX_COMMAND_ENV) == {"Path": "p", "TERM": "x"}
    assert inherit(source, HOOK_ENV) == {"Path": "p", "UserProfile": "u"}
    assert "OPENAI_API_KEY" not in inherit(source, CODING_CLI_ENV)


def test_a_protected_path_check_that_cannot_resolve_refuses(tmp_path):
    from odessy.authority import policy as module

    def broken(self, strict=False):
        raise OSError("cannot resolve")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "resolve", broken)
        refused = module._is_protected_path(tmp_path, "anything.txt", (tmp_path / "hooks.toml",))
    assert refused
