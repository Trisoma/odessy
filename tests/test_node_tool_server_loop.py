from pathlib import Path

import pytest

from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.models.fake_model import FakeModel
from odessy.tools.node_mcp import (
    build_node_tool_server_http,
    node_tool_server_dependencies_installed,
)
from odessy.authority.policy import OdessyPolicy


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_python_runtime_calls_node_filesystem_read_through_mcp(
    tmp_path: Path,
) -> None:
    if not node_tool_server_dependencies_installed():
        pytest.skip("Node tool server dependencies are not installed")

    (tmp_path / "README.md").write_bytes(b"Odessy Node MCP read\n")

    async with build_node_tool_server_http(tmp_path) as mcp_server_url:
        agent = OdessyAgent(
            model=FakeModel(),
            policy=OdessyPolicy(workspace_root=tmp_path),
            mcp_server=ExecutionTarget(mcp_server_url),
        )

        response = await agent.run_turn("read README.md")

    assert response.tool_request is not None
    assert response.tool_request.name == "filesystem.read"
    assert response.tool_result is not None
    assert response.tool_result["status"] == "ok"
    assert response.tool_result["content"] == "Odessy Node MCP read\n"
    assert "Odessy Node MCP read" in response.final_text
