"""Exercise wake -> transcript -> agent -> router without audio or model services."""

import asyncio
import contextlib

import pytest

from odessy.authority.policy import OdessyPolicy
from odessy.models.routed_model import RoutedModel, RoutingConfig
from odessy.runtime.agent_loop import OdessyAgent
from odessy.runtime.types import FinalResponse
from test_wake_session import Harness, SILENCE, SPEECH, WAKE


@pytest.mark.anyio
@pytest.mark.parametrize("scenario,expected", [
    ("fresh_hi", [("fast", "greeting")]),
    ("hi_with_history", [("capable", "conversation_context")]),
    ("gemma_failure", [("fast", "greeting"), ("capable", "fast_failed")]),
])
async def test_wake_transcript_routes_through_real_agent(tmp_path, scenario, expected):
    announcements = []
    model_calls = []
    received = []
    spoken = asyncio.Event()

    def announce(message):
        announcements.append(message)
        print(message)

    class ScriptedModel:
        def __init__(self, name, *, fail=False):
            self.model = name
            self.fail = fail

        def propose(self, text):
            # The user sees the selection before the adapter starts generating.
            assert self.model in announcements[-1]
            model_calls.append((self.model, text, self.conversation_context))
            if self.fail:
                raise RuntimeError("simulated Gemma failure")
            return FinalResponse(f"Hello from {self.model}")

    router = RoutedModel(
        RoutingConfig(),
        fast_factory=lambda: ScriptedModel("gemma-mock", fail=scenario == "gemma_failure"),
        capable_factory=lambda: ScriptedModel("lfm-q8-mock"),
        announce=announce,
    )
    agent = OdessyAgent(
        model=router, policy=OdessyPolicy(workspace_root=tmp_path), mcp_server=None,
    )
    harness = Harness([WAKE, *([SPEECH] * 4), *([SILENCE] * 6)], transcripts=["Hi"])
    session = harness.build()
    if scenario == "hi_with_history":
        session.memory.add("Previous question", "Previous answer")

    async def run_turn(text, stop, context):
        received.append((text, context))
        result = await agent.run_turn(text, stop_event=stop, context=context)
        return result.final_text

    async def speak(text):
        harness.spoken.append(text)
        spoken.set()

    session.run_turn = run_turn
    session.speak = speak
    task = asyncio.create_task(session.run())
    try:
        await asyncio.wait_for(spoken.wait(), timeout=5)
        assert session.error is None
        assert harness.cues == 1
        assert len(harness.transcribed) == 1
        assert [text for text, _ in received] == ["Hi"]
        assert bool(received[0][1].turns) == (scenario == "hi_with_history")
        assert len(announcements) == len(expected)
        for message, (role, reason) in zip(announcements, expected):
            assert f"role={role}" in message
            assert f"reason={reason}" in message
        assert [name for name, _, _ in model_calls] == [
            "gemma-mock" if role == "fast" else "lfm-q8-mock" for role, _ in expected
        ]
        assert all(text == "Hi" for _, text, _ in model_calls)
        if scenario == "hi_with_history":
            assert model_calls[0][2] == (
                {"role": "user", "content": "Previous question"},
                {"role": "assistant", "content": "Previous answer"},
            )
        assert harness.spoken == [f"Hello from {model_calls[-1][0]}"]
        print(f"{scenario}: transcript=Hi, history={bool(received[0][1].turns)}, "
              f"models={[name for name, _, _ in model_calls]}")
    finally:
        await session.aclose()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
