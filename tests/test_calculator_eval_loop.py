import pytest

from odessy.agent_loop import ExecutionTarget, OdessyAgent
from odessy.fake_model import FakeModel
from odessy.mcp_server import build_odessy_mcp_server
from odessy.policy import OdessyPolicy


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def build_agent() -> OdessyAgent:
    return OdessyAgent(
        model=FakeModel(),
        policy=OdessyPolicy(
            workspace_root=".",
            allowed_tools={"calculator.eval"},
        ),
        mcp_server=ExecutionTarget(build_odessy_mcp_server()),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("calculate 12 * 8", 96),
        ("calculate 137 * 29", 3973),
        ("calculate (12 + 5) / 17", 1.0),
    ],
)
async def test_calculator_eval_flows_through_policy_and_mcp(
    prompt: str,
    expected: int | float,
) -> None:
    agent = build_agent()

    response = await agent.run_turn(prompt)

    assert response.tool_request is not None
    assert response.tool_request.name == "calculator.eval"
    assert response.tool_result == {
        "expression": prompt.removeprefix("calculate ").strip(),
        "result": expected,
        "status": "ok",
    }
    assert str(expected) in response.final_text
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
@pytest.mark.parametrize(
    "prompt",
    [
        "calculate __import__('os').system('dir')",
        "calculate open('secret.txt').read()",
        "calculate (1).__class__",
    ],
)
async def test_calculator_eval_rejects_unsafe_input(prompt: str) -> None:
    agent = build_agent()

    with pytest.raises(RuntimeError, match="MCP tool returned an error"):
        await agent.run_turn(prompt)
