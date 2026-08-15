from pathlib import Path

import pytest

from odessy.agent_loop import OdessyAgent
from odessy.fake_model import FakeModel
from odessy.mcp_server import build_odessy_mcp_server
from odessy.policy import OdessyPolicy


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def build_agent(workspace_root: Path, confirm=False) -> OdessyAgent:
    return OdessyAgent(
        model=FakeModel(),
        policy=OdessyPolicy(workspace_root=workspace_root),
        mcp_server=build_odessy_mcp_server(workspace_root=workspace_root),
        confirm=confirm,
    )


@pytest.mark.anyio
async def test_terminal_run_pwd_flows_through_policy_and_mcp(tmp_path: Path) -> None:
    agent = build_agent(tmp_path)

    response = await agent.run_turn("run pwd")

    assert response.tool_request is not None
    assert response.tool_request.name == "terminal.run"
    assert response.tool_result is not None
    assert response.tool_result["exit_code"] == 0
    assert response.tool_result["status"] == "ok"
    assert response.tool_result["cwd"] == str(tmp_path.resolve())
    assert "exit code 0" in response.final_text
    assert [entry.event for entry in response.log] == [
        "user_input",
        "model_tool_request",
        "schema_validated",
        "policy_allowed",
        "mcp_tool_called",
        "tool_observation",
        "model_final_response",
    ]


@pytest.mark.anyio
async def test_terminal_run_ls_is_allowed_read_only(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text("hello", encoding="utf-8")
    agent = build_agent(tmp_path)

    response = await agent.run_turn("run ls")

    assert response.tool_result is not None
    assert response.tool_result["exit_code"] == 0
    assert "sample.txt" in response.tool_result["stdout"]


@pytest.mark.anyio
async def test_terminal_run_git_status_is_allowed_even_when_nonzero(
    tmp_path: Path,
) -> None:
    agent = build_agent(tmp_path)

    response = await agent.run_turn("run git status --definitely-invalid-option")

    assert response.tool_result is not None
    assert response.tool_result["exit_code"] != 0
    assert response.tool_result["status"] == "failed"
    assert "exit code" in response.final_text


@pytest.mark.anyio
async def test_terminal_run_requires_confirmation_for_file_modification(
    tmp_path: Path,
) -> None:
    agent = build_agent(tmp_path)

    with pytest.raises(PermissionError, match="requires confirmation"):
        await agent.run_turn("run touch created.txt")


@pytest.mark.anyio
async def test_terminal_run_executes_confirmed_file_modification(
    tmp_path: Path,
) -> None:
    agent = build_agent(tmp_path, confirm=lambda decision, request: True)

    response = await agent.run_turn(
        """run python -c "open('created.txt','w').write('ok')" """
    )

    assert response.tool_result is not None
    assert response.tool_result["exit_code"] == 0
    assert (tmp_path / "created.txt").exists()
    assert "policy_confirmed" in [entry.event for entry in response.log]


@pytest.mark.anyio
async def test_terminal_run_denies_destructive_command(tmp_path: Path) -> None:
    agent = build_agent(tmp_path)

    with pytest.raises(PermissionError, match="denied"):
        await agent.run_turn("run rm -rf .")


@pytest.mark.anyio
async def test_terminal_run_denies_workspace_escape(tmp_path: Path) -> None:
    agent = OdessyAgent(
        model=FakeModel(),
        policy=OdessyPolicy(workspace_root=tmp_path),
        mcp_server=build_odessy_mcp_server(workspace_root=tmp_path),
    )

    with pytest.raises(PermissionError, match="outside the allowed workspace"):
        await agent.run_turn("run --cwd .. pwd")
