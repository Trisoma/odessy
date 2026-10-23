import asyncio
from types import SimpleNamespace

import pytest
from mcp.server import MCPServer

from odessy.authority.policy import OdessyPolicy
from odessy.config.deployment import model_tool_names
from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.runtime.types import FinalResponse, ToolObservation, ToolRequest


def test_collaboration_tools_are_opt_in_and_policy_checked(tmp_path):
    from odessy.collaboration.context import collaboration_scope

    assert "model.consult" not in model_tool_names()
    profile = SimpleNamespace(capabilities=("consult", "delegate"), allowed_modes=("advice",))
    service = SimpleNamespace(config=SimpleNamespace(
        settings=SimpleNamespace(enabled=True, automatic=True, default_participant="peer"),
        participants={"peer": profile},
    ))
    policy = OdessyPolicy(workspace_root=tmp_path)
    request = ToolRequest("model.consult", {"participant": "peer", "question": "Another opinion?"})
    assert policy.evaluate(request).action == "deny"
    with collaboration_scope(service):
        assert "model.consult" in model_tool_names()
        assert policy.evaluate(request).allowed
        assert policy.evaluate(ToolRequest("agent.delegate", {
            "participant": "peer", "task": "Review this", "mode": "advice",
        })).requires_confirmation
        assert policy.evaluate(ToolRequest("model.consult", {
            "participant": "missing", "question": "Another opinion?",
        })).action == "deny"


@pytest.mark.anyio
async def test_async_confirmation_is_awaited_before_tool_dispatch(tmp_path):
    approved = asyncio.Event()
    server = MCPServer("approval-test")

    @server.tool(name="filesystem.write")
    def write(path: str, content: str) -> dict:
        assert approved.is_set(), "an unawaited coroutine must never count as approval"
        return {"status": "ok", "path": path}

    class Model:
        def propose(self, text):
            return ToolRequest("filesystem.write", {"path": "note.txt", "content": "test"})

        def respond_to_observation(self, text, observation):
            return FinalResponse("Written")

    async def confirm(decision, request):
        await asyncio.sleep(0)
        approved.set()
        return True

    agent = OdessyAgent(model=Model(), policy=OdessyPolicy(workspace_root=tmp_path),
                        mcp_server=ExecutionTarget(server), confirm=confirm)
    result = await agent.run_turn("write a note")
    assert approved.is_set()
    assert result.final_text == "Written"


@pytest.mark.anyio
async def test_explicit_peer_observation_is_attributed_reference_context(tmp_path):
    seen = []

    class Coordinator:
        def propose(self, text, *, context=None):
            seen.append((text, context))
            return FinalResponse("My synthesis")

    agent = OdessyAgent(model=Coordinator(), policy=OdessyPolicy(workspace_root=tmp_path), mcp_server=None)
    observation = ToolObservation(ToolRequest("model.consult", {"participant": "peer"}),
                                  {"status": "ok", "participant": "peer", "answer": "Peer opinion"})
    result = await agent.run_turn("Original question", initial_observation=observation)
    assert result.final_text == "My synthesis"
    assert seen[0][0] == "Original question"
    assert "peer" in seen[0][1].turns[-1].assistant
    assert "untrusted" in seen[0][1].turns[-1].user.lower()


@pytest.mark.anyio
async def test_model_consultation_uses_real_policy_mcp_and_local_synthesis(tmp_path):
    from odessy.collaboration.config import CollaborationConfig, CollaborationSettings, ParticipantProfile
    from odessy.collaboration.context import collaboration_scope
    from odessy.collaboration.participants import CollaborationService
    from odessy.collaboration.tools import build_collaboration_server

    service = CollaborationService(CollaborationConfig(
        CollaborationSettings(enabled=True, automatic=True, default_participant="peer"),
        {"peer": ParticipantProfile("peer", "ollama")}),
        adapters={"peer": lambda profile, question, context: "Peer suggests a garden walk"})
    seen = []

    class Coordinator:
        def propose(self, text):
            return ToolRequest("model.consult", {"participant": "peer", "question": text})

        def respond_to_observation(self, text, observation):
            seen.append(observation)
            return FinalResponse("My synthesis: take a short garden walk.")

    service.start_turn("How should I spend my afternoon?")
    agent = OdessyAgent(model=Coordinator(), policy=OdessyPolicy(workspace_root=tmp_path),
                        mcp_server=ExecutionTarget(build_collaboration_server(service)))
    with collaboration_scope(service):
        result = await agent.run_turn("How should I spend my afternoon?")
    assert seen[0].result["participant"] == "peer"
    assert seen[0].result["answer"] == "Peer suggests a garden walk"
    assert result.final_text.startswith("My synthesis:")
