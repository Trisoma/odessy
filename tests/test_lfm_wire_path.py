"""Real-adapter wire-path tests: HTTP request -> streamed NDJSON -> parse -> loop.

Why this file exists
--------------------

Every other LFM adapter test subclasses ``LlamaCppLFMModel`` and overrides
``_chat`` (see ``QueuedLFM`` in ``test_lfm_model.py``). That bypasses three
things at once:

```text
1. the request payload actually sent   (so `tools=` is never inspected on the wire)
2. the streamed NDJSON accumulation    (_accumulate_stream)
3. the response extraction             (_extract_model_action_from_chat)
```

Layer 1 is where the ``tools=`` blocker hid: the scripted doubles returned tool
calls regardless of whether tools were advertised, so 14 tests passed while
continuation was inert against a real model. A test that cannot fail for the
reason the feature was broken is not evidence.

These tests fake only ``urllib.request.urlopen``. The request payload, the
streamed accumulation, the parsing, and in the loop test the policy and the real
tool execution are all production code.

The NDJSON fixtures are the shape captured from the live model on the Jetson
(``lfm2.5-thinking:1.2b-q8_0``, 2026-09-23), not invented: one terminal chunk
carrying a complete ``tool_calls`` array whose ``arguments`` is a JSON object
rather than a string.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from odessy.runtime.agent_loop import ExecutionTarget, OdessyAgent
from odessy.models.lfm_model import LlamaCppLFMModel
from odessy.tools.mcp_server import build_odessy_mcp_server
from odessy.authority.policy import OdessyPolicy
from odessy.runtime.types import FinalResponse, ToolObservation, ToolRequest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _line(payload: dict) -> str:
    return json.dumps(payload) + "\n"


def _thinking(text: str) -> str:
    return _line(
        {"message": {"role": "assistant", "content": "", "thinking": text}, "done": False}
    )


def _tool_call(name: str, arguments: dict) -> str:
    """The exact streamed tool-call shape observed from the live model."""

    return _line(
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_7pi1f82o",
                        "function": {"index": 0, "name": name, "arguments": arguments},
                    }
                ],
            },
            "done": False,
        }
    )


def _final(text: str) -> str:
    """A final answer in the documented model contract.

    Deliberately the JSON form rather than bare prose: bare prose is not valid
    model output, so it would trigger the adapter's repair path and consume an
    extra HTTP call, making the request-count assertions meaningless.
    """

    return _line(
        {
            "message": {
                "role": "assistant",
                "content": json.dumps({"text": text}),
            },
            "done": False,
        }
    )


def _done(eval_count: int = 42) -> str:
    return _line(
        {"message": {"role": "assistant", "content": ""}, "done": True, "eval_count": eval_count}
    )


class _StreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __enter__(self) -> "_StreamResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def __iter__(self):
        for line in self._lines:
            yield line.encode("utf-8")


class FakeOllama:
    """Serves queued NDJSON streams and records every request payload."""

    def __init__(self, streams: list[list[str]]) -> None:
        self._streams = [list(stream) for stream in streams]
        self.requests: list[dict] = []

    def __call__(self, request, timeout=None):  # noqa: ANN001 - urlopen signature
        self.requests.append(json.loads(request.data.decode("utf-8")))
        if not self._streams:
            raise AssertionError("no queued stream left for this call")
        return _StreamResponse(self._streams.pop(0))


def _model(monkeypatch: pytest.MonkeyPatch, fake: FakeOllama) -> LlamaCppLFMModel:
    monkeypatch.setattr("urllib.request.urlopen", fake)
    return LlamaCppLFMModel(endpoint="http://127.0.0.1:11434")


# --------------------------------------------------------------------------
# Adapter-level: the real _chat path
# --------------------------------------------------------------------------


def test_propose_streams_and_parses_a_real_tool_call(monkeypatch) -> None:
    fake = FakeOllama(
        [
            [
                _thinking("Oka"),
                _tool_call("terminal.run", {"command": "pwd", "cwd": ".", "timeout": 10}),
                _done(),
            ]
        ]
    )
    model = _model(monkeypatch, fake)

    action = model.propose("run pwd")

    assert isinstance(action, ToolRequest)
    assert action.name == "terminal.run"
    assert action.arguments == {"command": "pwd", "cwd": ".", "timeout": 10}
    # The request really was a streaming request, and it advertised tools.
    assert fake.requests[0]["stream"] is True
    assert fake.requests[0]["tools"], "propose must advertise tools"


def test_observation_call_advertises_tools_at_the_wire_level(monkeypatch) -> None:
    """The wire-level proof of the `tools=` fix.

    Existing tests assert the ``tools=`` argument at the ``_chat`` signature,
    which a double can satisfy without any tools reaching the wire. This asserts
    the serialized request body.
    """

    fake = FakeOllama(
        [
            [
                _tool_call("terminal.run", {"command": "pwd", "cwd": ".", "timeout": 10}),
                _done(),
            ],
            [
                _tool_call("terminal.run", {"command": "whoami", "cwd": ".", "timeout": 10}),
                _done(),
            ],
        ]
    )
    model = _model(monkeypatch, fake)

    first = model.propose("run pwd")
    assert isinstance(first, ToolRequest)

    second = model.respond_to_observation(
        "run pwd",
        ToolObservation(
            request=first,
            result={
                "stdout": "x\n",
                "stderr": "",
                "exit_code": 0,
                "status": "ok",
                "timed_out": False,
            },
        ),
    )

    assert isinstance(second, ToolRequest), "a second tool call must survive the observation call"
    assert second.arguments["command"] == "whoami"

    assert len(fake.requests) == 2
    assert fake.requests[1]["tools"], "the observation request must advertise tools"
    # Every tool on both steps: the next step may need a different tool.
    assert fake.requests[1]["tools"] == fake.requests[0]["tools"]


def test_plain_final_answer_parses_without_a_repair_call(monkeypatch) -> None:
    fake = FakeOllama([[_thinking("hmm"), _final("It is noon."), _done()]])
    model = _model(monkeypatch, fake)

    action = model.propose("what time is it")

    assert isinstance(action, FinalResponse)
    assert action.text == "It is noon."
    assert len(fake.requests) == 1, "a valid final answer must not trigger repair"


# --------------------------------------------------------------------------
# Loop-level: real adapter + real policy + real execution
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_real_adapter_drives_a_second_tool_call_through_the_loop(
    monkeypatch, tmp_path: Path
) -> None:
    """Multi-tool continuation, with only Ollama's HTTP faked.

    This is the end-to-end shape the benchmark could not show: every benchmark
    sample recorded ``model_call_count = 1``, so continuation never fired on the
    real model. Here the loop must make a second model call, and that call must
    carry tools.
    """

    fake = FakeOllama(
        [
            [
                _tool_call("terminal.run", {"command": "pwd", "cwd": ".", "timeout": 10}),
                _done(),
            ],
            [
                _tool_call("terminal.run", {"command": "whoami", "cwd": ".", "timeout": 10}),
                _done(),
            ],
            [_final("Ran both commands."), _done()],
        ]
    )
    model = _model(monkeypatch, fake)
    # `confirm=True` on purpose: the second command is not on the low-risk allow
    # list, so the default configuration correctly demands confirmation and the
    # turn raises PermissionError. This test is about continuation, not about
    # authority -- and authority behaviour is asserted, with confirmation
    # refused, in test_authority_boundary.py and test_tool_rejection_loop.py.
    # Auto-approving here keeps the continuation claim testable without
    # weakening those assertions.
    agent = OdessyAgent(
        model=model,
        policy=OdessyPolicy(workspace_root=tmp_path),
        mcp_server=ExecutionTarget(build_odessy_mcp_server(workspace_root=tmp_path)),
        confirm=True,
    )

    response = await agent.run_turn("run pwd")

    assert len(fake.requests) == 3, (
        "expected proposal, one continuation after execution, then a final answer"
    )
    assert fake.requests[1]["tools"], "the continuation call must advertise tools"
    assert fake.requests[2]["tools"], "the final-answer call must advertise tools too"

    # Pin the CURRENT behaviour, which is a defect rather than a contract.
    #
    # On the observation path the model's raw content is passed through as
    # final_text without unwrapping the documented {"text": ...} envelope, so
    # literal JSON reaches the caller and therefore TTS. `propose()` does unwrap
    # it, so the two paths disagree. The same symptom was observed live during
    # the voice smoke test, where the final text contained
    # `{"iso_time": "...", "timezone": "local"}` and produced unnatural speech.
    #
    # Recorded here rather than fixed, so the disagreement is visible instead of
    # assumed. Fixing it is an adapter behaviour change that would need its own
    # decision and its own tests.
    assert response.final_text == json.dumps({"text": "Ran both commands."})
