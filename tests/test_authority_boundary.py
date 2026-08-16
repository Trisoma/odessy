from __future__ import annotations

import os
from pathlib import Path

import pytest

from odessy.agent_loop import OdessyAgent
from odessy.command_bridge import CommandBridge
from odessy.fake_model import FakeModel
from odessy.policy import OdessyPolicy
from odessy.types import ToolRequest
from odessy.workspace_authority import WorkspaceAuthority, WorkspaceAuthorityError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_workspace_authority_allows_relative_and_absolute_inside_paths(tmp_path: Path) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir()
    source.write_text("print('ok')\n", encoding="utf-8")
    authority = WorkspaceAuthority(tmp_path)

    assert authority.resolve_path("src/main.py", must_exist=True).relative == "src/main.py"
    assert authority.resolve_path(str(source), must_exist=True).relative == "src/main.py"


def test_workspace_authority_denies_outside_and_sibling_prefix_paths(tmp_path: Path) -> None:
    sibling = tmp_path.parent / f"{tmp_path.name}_sibling"
    sibling.mkdir(exist_ok=True)
    authority = WorkspaceAuthority(tmp_path)

    with pytest.raises(WorkspaceAuthorityError):
        authority.resolve_path("../outside.txt")
    with pytest.raises(WorkspaceAuthorityError):
        authority.resolve_path(str(sibling))


def test_workspace_authority_validates_nonexistent_target_by_existing_ancestor(
    tmp_path: Path,
) -> None:
    authority = WorkspaceAuthority(tmp_path)

    resolved = authority.resolve_path("new/note.txt", must_exist=False)

    assert resolved.relative == "new/note.txt"
    assert resolved.exists is False


def test_workspace_authority_denies_symlink_escape_when_supported(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available in this environment")

    authority = WorkspaceAuthority(tmp_path)

    with pytest.raises(WorkspaceAuthorityError):
        authority.resolve_path("link/secret.txt", must_exist=True)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "id_rsa",
        "deploy.pem",
        "private.key",
        "credentials.json",
        "api-token.txt",
        ".ssh/config",
        ".aws/credentials",
    ],
)
def test_policy_denies_sensitive_workspace_paths(tmp_path: Path, path: str) -> None:
    policy = OdessyPolicy(workspace_root=tmp_path)

    decision = policy.evaluate(ToolRequest("filesystem.read", {"path": path}))

    assert decision.action == "deny"
    assert "credentials or secrets" in decision.reason


def test_policy_allows_normal_workspace_source_path(tmp_path: Path) -> None:
    policy = OdessyPolicy(workspace_root=tmp_path)

    decision = policy.evaluate(ToolRequest("filesystem.read", {"path": "src/main.py"}))

    assert decision.action == "allow"


def test_policy_denies_background_or_detached_terminal_requests(tmp_path: Path) -> None:
    policy = OdessyPolicy(workspace_root=tmp_path)

    for command in ["nohup python server.py &", "Start-Process notepad", "cmd /c start calc"]:
        decision = policy.evaluate(
            ToolRequest("terminal.run", {"command": command, "cwd": ".", "timeout": 10})
        )
        assert decision.action == "deny"
        assert "background or detached" in decision.reason


def test_command_bridge_uses_sanitized_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODESSY_TEST_SECRET", "secret-value")
    bridge = CommandBridge(tmp_path)
    command = "Get-ChildItem Env:" if os.name == "nt" else "env"

    result = bridge.run(command=command, timeout=10)

    assert result["status"] == "ok"
    assert "ODESSY_TEST_SECRET" not in result["stdout"]
    assert "secret-value" not in result["stdout"]


def test_command_bridge_bounds_stdout(tmp_path: Path) -> None:
    bridge = CommandBridge(tmp_path, max_stdout_chars=100)
    command = "python -c \"print('x' * 1000)\""

    result = bridge.run(command=command, timeout=10)

    assert result["stdout_truncated"] is True
    assert len(result["stdout"]) < 200


def test_command_bridge_times_out_request_scoped_process(tmp_path: Path) -> None:
    bridge = CommandBridge(tmp_path)

    result = bridge.run(command="python -c \"import time; time.sleep(5)\"", timeout=1)

    assert result["status"] == "timeout"
    assert result["timed_out"] is True


class _UnknownToolModel:
    def propose(self, user_text: str) -> ToolRequest:
        return ToolRequest("unknown.tool", {})

    def respond_to_observation(self, user_text, observation):  # pragma: no cover
        raise AssertionError("denied requests must not reach observation handling")


@pytest.mark.anyio
async def test_policy_denial_does_not_contact_mcp(tmp_path: Path) -> None:
    agent = OdessyAgent(
        model=_UnknownToolModel(),
        policy=OdessyPolicy(workspace_root=tmp_path),
        mcp_server=object(),
    )

    with pytest.raises(PermissionError, match="not allowed"):
        await agent.run_turn("try unknown")


@pytest.mark.anyio
async def test_policy_denies_sensitive_request_before_mcp(tmp_path: Path) -> None:
    agent = OdessyAgent(
        model=FakeModel(),
        policy=OdessyPolicy(workspace_root=tmp_path),
        mcp_server=object(),
    )

    with pytest.raises(PermissionError, match="credentials or secrets"):
        await agent.run_turn("read .env")
