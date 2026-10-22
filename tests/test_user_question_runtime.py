import asyncio
import pytest
from mcp.server import MCPServer

from odessy.config.deployment import model_tool_names
from odessy.interaction.context import interaction_scope
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.runtime.types import FinalResponse, ToolRequest
from odessy.authority.policy import OdessyPolicy
from odessy.tools.tool_contracts import validate_tool_arguments, build_openai_tools


def test_question_tool_is_only_advertised_with_a_human_channel():
    assert 'user.ask' not in model_tool_names()
    with interaction_scope(object()):
        assert 'user.ask' in model_tool_names()
        schema = next(t for t in build_openai_tools() if t['function']['name'] == 'user.ask')
        assert schema['function']['parameters']['properties']['choices']['items']['type'] == 'string'
        validate_tool_arguments('user.ask', {'question': 'Which repository?', 'choices': ['Odessy', 'ICG']})
        with pytest.raises(ValueError):
            validate_tool_arguments('user.ask', {'question': 'Which?', 'choices': [False]})
        with pytest.raises(ValueError):
            validate_tool_arguments('user.ask', {'question': 'Which?', 'turn_id': 'model-chosen'})


@pytest.mark.anyio
async def test_question_answer_continues_the_same_agent_turn(tmp_path):
    server = MCPServer('human-test')
    seen = []

    @server.tool(name='user.ask')
    async def ask(question: str, choices: list[str] | None = None, allow_free_text: bool = True) -> dict[str, object]:
        return {'status': 'ok', 'source': 'human_information', 'answer': 'Odessy'}

    class Model:
        def propose(self, text):
            return ToolRequest('user.ask', {'question': 'Which repository?', 'choices': ['Odessy', 'ICG']})
        def respond_to_observation(self, text, observation):
            seen.append(observation)
            return FinalResponse('I will use Odessy.')

    agent = OdessyAgent(model=Model(), policy=OdessyPolicy(workspace_root=tmp_path),
                        mcp_server=ExecutionTarget(server))
    with interaction_scope(object()):
        result = await agent.run_turn('Review my repository')
    assert result.final_text == 'I will use Odessy.'
    assert seen[0].result['source'] == 'human_information'
    assert seen[0].result['answer'] == 'Odessy'


@pytest.mark.anyio
async def test_failed_question_stops_later_batch_calls(tmp_path):
    from odessy.runtime.types import ToolBatch, ToolCall
    server = MCPServer('failed-question')
    called = []

    @server.tool(name='user.ask')
    async def ask(question: str) -> dict[str, object]:
        raise RuntimeError('microphone unavailable')

    @server.tool(name='time.now')
    async def now() -> dict[str, object]:
        called.append(True)
        return {'time': 'now'}

    class Model:
        def propose(self, text):
            return ToolBatch((ToolCall('q1', ToolRequest('user.ask', {'question': 'Which?'})),
                              ToolCall('later', ToolRequest('time.now'))))
        def respond_to_observations(self, *args):
            raise AssertionError('Missing clarification must not be guessed')

    agent = OdessyAgent(model=Model(), policy=OdessyPolicy(tmp_path), mcp_server=ExecutionTarget(server))
    with interaction_scope(object()):
        result = await agent.run_turn('Help')
    assert not called
    assert result.tool_observations[0].result['status'] == 'denied'
    assert result.tool_observations[1].result['status'] == 'skipped'
