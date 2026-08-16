from pathlib import Path

from odessy.policy import OdessyPolicy
from odessy.types import ToolRequest


def test_read_only_workspace_tools_are_low_risk_allowed(tmp_path: Path) -> None:
    policy = OdessyPolicy(workspace_root=tmp_path)

    for tool_name, arguments in [
        ("filesystem.read", {"path": "README.md"}),
        ("filesystem.list", {"path": "."}),
        ("project.find", {"pattern": "policy", "path": "."}),
        ("project.grep", {"pattern": "ALLOW", "path": "."}),
        ("git.status", {}),
        ("git.diff", {"mode": "working"}),
        ("git.changed_files", {"mode": "all"}),
        ("git.log", {"limit": 20}),
    ]:
        decision = policy.evaluate(ToolRequest(name=tool_name, arguments=arguments))

        assert decision.action == "allow"
        assert decision.risk == "low"


def test_read_only_policy_denies_workspace_escape(tmp_path: Path) -> None:
    policy = OdessyPolicy(workspace_root=tmp_path)

    decision = policy.evaluate(
        ToolRequest(name="filesystem.read", arguments={"path": "../secret.txt"})
    )

    assert decision.action == "deny"
    assert "outside the allowed workspace" in decision.reason


def test_read_only_policy_denies_secret_paths(tmp_path: Path) -> None:
    policy = OdessyPolicy(workspace_root=tmp_path)

    decision = policy.evaluate(
        ToolRequest(name="filesystem.read", arguments={"path": ".ssh/id_rsa"})
    )

    assert decision.action == "deny"
    assert "credentials or secrets" in decision.reason


def test_write_tools_remain_denied_in_step_6(tmp_path: Path) -> None:
    policy = OdessyPolicy(workspace_root=tmp_path)

    decision = policy.evaluate(
        ToolRequest(name="filesystem.write", arguments={"path": "notes.txt", "content": "x"})
    )

    assert decision.action == "deny"
    assert "not allowed" in decision.reason
