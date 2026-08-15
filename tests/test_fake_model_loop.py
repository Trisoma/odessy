import pytest

from odessy.agent_loop import OdessyAgent
from odessy.fake_model import FakeModel
from odessy.mcp_server import build_odessy_mcp_server
from odessy.policy import OdessyPolicy


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_fake_model_time_request_flows_through_policy_and_mcp() -> None:
    from pathlib import Path

    agent = OdessyAgent(
        model=FakeModel(),
        policy=OdessyPolicy(
            workspace_root=Path.cwd(),
            allowed_tools={"time.now"},
        ),
        mcp_server=build_odessy_mcp_server(
            clock=lambda: "2026-08-15T23:45:00+08:00"
        ),
    )

    response = await agent.run_turn("What time is it?")

    assert response.final_text == "The current time is 2026-08-15T23:45:00+08:00."
    assert response.tool_request is not None
    assert response.tool_request.name == "time.now"
    assert response.tool_result == {
        "iso_time": "2026-08-15T23:45:00+08:00",
        "timezone": "local",
    }
    assert [entry.event for entry in response.log] == [
        "user_input",
        "model_tool_request",
        "schema_validated",
        "policy_allowed",
        "mcp_tool_called",
        "tool_observation",
        "model_final_response",
    ]
