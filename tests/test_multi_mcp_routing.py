from __future__ import annotations

from pathlib import Path

import pytest

from mcp.server import MCPServer

from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.models.fake_model import FakeModel
from odessy.tools.mcp_server import build_odessy_mcp_server
from odessy.authority.policy import OdessyPolicy


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_agent_routes_tool_calls_across_multiple_mcp_servers(tmp_path: Path) -> None:
    extra_server = MCPServer("odessy-extra-tools")

    @extra_server.tool(
        name="filesystem.read",
        description="Read a workspace file from the extra server.",
    )
    def filesystem_read(path: str) -> dict[str, object]:
        return {
            "path": path,
            "content": "hello from the extra server\n",
            "size": 28,
            "status": "ok",
        }

    agent = OdessyAgent(
        model=FakeModel(),
        policy=OdessyPolicy(workspace_root=tmp_path),
        mcp_server=(
            ExecutionTarget(build_odessy_mcp_server(workspace_root=tmp_path)),
            ExecutionTarget(extra_server),
        ),
    )

    response = await agent.run_turn("read README.md")

    assert response.tool_request is not None
    assert response.tool_request.name == "filesystem.read"
    assert response.tool_result is not None
    assert response.tool_result["content"] == "hello from the extra server\n"
    assert "extra server" in response.final_text
