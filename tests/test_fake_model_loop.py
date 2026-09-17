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
    assert response.metrics.tool_called is True
    assert response.metrics.proposal_valid is True
    assert response.metrics.repair_triggered is False
    assert response.metrics.agent_turn_total_ms is not None
    assert response.metrics.proposal_generation_ms is not None
    assert response.metrics.policy_validation_ms is not None
    assert response.metrics.mcp_execution_ms is not None
    assert response.metrics.final_generation_ms is not None
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
        "agent_turn_metrics",
    ]


@pytest.mark.anyio
async def test_agent_reports_direct_response_metrics() -> None:
    from pathlib import Path
    from odessy.types import FinalResponse

    class DirectModel:
        def propose(self, user_text: str):
            return FinalResponse(text="hello")

        def respond_to_observation(self, user_text, observation):
            raise AssertionError("direct response should not request observation response")

    agent = OdessyAgent(
        model=DirectModel(),
        policy=OdessyPolicy(workspace_root=Path.cwd()),
        mcp_server=None,
    )

    response = await agent.run_turn("hi")

    assert response.final_text == "hello"
    assert response.metrics.tool_called is False
    assert response.metrics.proposal_valid is True
    assert response.metrics.repair_triggered is False
    assert response.metrics.agent_turn_total_ms is not None
    assert response.metrics.proposal_generation_ms is not None
    assert response.metrics.policy_validation_ms is None
    assert response.metrics.mcp_execution_ms is None
    assert response.metrics.final_generation_ms is None
    assert [entry.event for entry in response.log] == [
        "user_input",
        "model_final_response",
        "agent_turn_metrics",
    ]
