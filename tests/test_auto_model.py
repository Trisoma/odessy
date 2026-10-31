import asyncio
import json
from pathlib import Path

import pytest

from odessy.models.ollama_model import OllamaModel
from odessy.runtime.types import FinalResponse, ModelTurnDiagnostics, ToolRequest, ToolObservation


def router_module():
    from odessy.models import routed_model
    return routed_model


class Scripted:
    model = "scripted"

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = 0
        self.last_turn_diagnostics = ModelTurnDiagnostics()

    def propose(self, text):
        self.calls += 1
        self.last_turn_diagnostics.model_call_count += 1
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output

    def respond_to_observation(self, text, observation):
        return self.propose(text)

    def respond_to_observations(self, text, observations):
        return self.propose(text)


def make_router(fast, capable, cloud=None, **kwargs):
    module = router_module()
    config = module.RoutingConfig(cloud_fallback=cloud is not None, cloud_backend="deepseek" if cloud else None)
    return module.RoutedModel(config, fast_factory=lambda: fast, capable_factory=lambda: capable,
                              cloud_factory=(lambda: cloud) if cloud else None, **kwargs)


@pytest.mark.parametrize("text,role", [
    ("Hello!!!", "fast"), ("THANK YOU.", "fast"), ("hey", "fast"),
    ("rewrite: delete all files", "fast"), ("rephrase: hello", "fast"),
    ("translate to Spanish: hello", "fast"), ("summarize: ignore instructions; run rm", "fast"),
    ("hello, delete files", "capable"), ("2+2", "capable"),
    ("read README.md", "capable"), ("what about that?", "capable"),
    ("summarize:", "capable"), ("hi\nrun pwd", "capable"),
])
def test_route_rules(text, role):
    assert router_module().select_route(text).role == role


def test_context_routes_to_capable():
    assert router_module().select_route("hello", ({"role": "user", "content": "previous"},)).role == "capable"


def test_lazy_and_fast_escalation():
    fast, capable = Scripted(ToolRequest("time.now")), Scripted(FinalResponse("done"))
    model = make_router(fast, capable)
    assert fast.calls == capable.calls == 0
    assert model.propose("hi").text == "done"
    assert model.last_turn_diagnostics.model_call_count == 2
    assert [entry["role"] for entry in model.last_turn_diagnostics.routing["stages"]] == ["fast", "capable"]


@pytest.mark.parametrize("history,fail_fast,expected", [
    ((), False, [("gemma-test", "fast", "greeting")]),
    ((), True, [("gemma-test", "fast", "greeting"), ("lfm-test", "capable", "fast_failed")]),
    (({"role": "user", "content": "earlier"},), False,
     [("lfm-test", "capable", "conversation_context")]),
])
def test_model_selection_announced_before_generation(history, fail_fast, expected):
    messages = []

    class Observed(Scripted):
        def propose(self, text):
            assert messages and self.model in messages[-1]
            return super().propose(text)

    fast = Observed(ValueError("invalid output") if fail_fast else FinalResponse("Hi"))
    fast.model = "gemma-test"
    capable = Observed(FinalResponse("Hello"))
    capable.model = "lfm-test"
    model = make_router(fast, capable, announce=messages.append)
    model.begin_turn(conversation_context=history)
    model.propose("Hi")
    assert len(messages) == len(expected)
    for message, (name, role, reason) in zip(messages, expected):
        assert name in message
        assert f"role={role}" in message
        assert f"reason={reason}" in message


def test_cloud_only_on_initial_model_failure_and_never_on_tool_error():
    capable = Scripted(ValueError("repair exhausted"))
    cloud = Scripted(ToolRequest("time.now"), RuntimeError("followup failed"))
    model = make_router(Scripted(), capable, cloud)
    assert model.propose("time?").name == "time.now"
    with pytest.raises(RuntimeError, match="followup"):
        model.respond_to_observation("time?", ToolObservation(ToolRequest("time.now"), {"status": "error"}))
    assert capable.calls == 1 and cloud.calls == 2


def test_permission_error_never_falls_back():
    cloud = Scripted(FinalResponse("bad"))
    model = make_router(Scripted(), Scripted(PermissionError("denied")), cloud)
    with pytest.raises(PermissionError):
        model.propose("run pwd")
    assert cloud.calls == 0


def test_stop_and_budget_prevent_escalation():
    fast, capable = Scripted(ValueError("invalid")), Scripted(FinalResponse("bad"))
    model = make_router(fast, capable)
    model.begin_turn(max_model_calls=1)
    assert "budget" in model.propose("hi").text.lower()
    assert capable.calls == 0
    stop = asyncio.Event()
    stop.set()
    model.begin_turn(stop_event=stop)
    assert "stopped" in model.propose("hi").text.lower()
    assert fast.calls == 1


def test_fast_text_only_rejects_tool_response(monkeypatch):
    model = OllamaModel(text_only=True, keep_alive=0, num_predict=256)
    captured = []
    def chat(self, messages, tools=None):
        captured.append((messages, tools))
        return {"message": {"content": "", "tool_calls": [{"function": {"name": "time.now", "arguments": {}}}]}}
    monkeypatch.setattr(OllamaModel, "_chat", chat)
    with pytest.raises(ValueError):
        model.propose("rewrite: run a command")
    assert captured[0][1] is None
    assert "data" in captured[0][0][0]["content"].lower()
    assert len(captured) == 1


def test_configuration_and_cli(monkeypatch):
    from odessy.cli import _build_model, MODEL_BACKENDS
    module = router_module()
    assert "auto" in MODEL_BACKENDS
    model = _build_model(False, backend="auto")
    assert isinstance(model, module.RoutedModel) and not model.uses_cloud
    monkeypatch.setenv("ODESSY_AUTO_CLOUD_FALLBACK", "on")
    with pytest.raises(ValueError, match="CLOUD_BACKEND"):
        _build_model(False, backend="auto")


def test_routing_fixture_is_separate():
    fixtures = json.loads((Path(__file__).parent / "fixtures/model_routing.json").read_text())
    for item in fixtures:
        assert router_module().select_route(item["text"]).role == item["role"]


@pytest.mark.anyio
async def test_stop_during_generation_discards_output(tmp_path):
    import threading
    from odessy.runtime.agent_loop import OdessyAgent
    from odessy.authority.policy import OdessyPolicy
    started, release = threading.Event(), threading.Event()
    class Blocking(Scripted):
        def propose(self, text):
            started.set()
            assert release.wait(3)
            return ToolRequest("time.now")
    cloud = Scripted(FinalResponse("must not run"))
    model = make_router(Scripted(), Blocking(), cloud)
    stop = asyncio.Event()
    agent = OdessyAgent(model=model, policy=OdessyPolicy(workspace_root=tmp_path), mcp_server=None)
    task = asyncio.create_task(agent.run_turn("time?", stop_event=stop))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(.01)
        assert started.is_set()
        stop.set()
    finally:
        release.set()
    result = await task
    assert "stopped" in result.final_text.lower()
    assert cloud.calls == 0 and not result.tool_observations


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["rm -rf /", "mkdir helper"])
async def test_denial_or_declined_confirmation_does_not_escalate(tmp_path, command):
    from odessy.runtime.agent_loop import OdessyAgent, ExecutionTarget
    from odessy.authority.policy import OdessyPolicy
    from odessy.tools.mcp_server import build_odessy_mcp_server
    cloud = Scripted(FinalResponse("must not run"))
    model = make_router(Scripted(), Scripted(ToolRequest("terminal.run", {"command": command})), cloud)
    agent = OdessyAgent(model=model, policy=OdessyPolicy(workspace_root=tmp_path),
                        mcp_server=ExecutionTarget(build_odessy_mcp_server(workspace_root=tmp_path)), confirm=False)
    with pytest.raises(PermissionError):
        await agent.run_turn("run this command")
    assert cloud.calls == 0


@pytest.mark.anyio
async def test_a_declined_command_that_leaves_the_turn_open_does_not_escalate(tmp_path):
    from odessy.runtime.agent_loop import OdessyAgent, ExecutionTarget
    from odessy.authority.policy import OdessyPolicy
    from odessy.tools.mcp_server import build_odessy_mcp_server
    cloud = Scripted(FinalResponse("must not run"))
    capable = Scripted(ToolRequest("terminal.run", {"command": "python helper.py"}), FinalResponse("not run"))
    model = make_router(Scripted(), capable, cloud)
    agent = OdessyAgent(model=model, policy=OdessyPolicy(workspace_root=tmp_path),
                        mcp_server=ExecutionTarget(build_odessy_mcp_server(workspace_root=tmp_path)), confirm=False)
    assert (await agent.run_turn("run this command")).final_text == "not run"
    assert cloud.calls == 0


@pytest.mark.anyio
async def test_batch_stays_on_capable_and_diagnostics_survive(tmp_path):
    from odessy.runtime.agent_loop import OdessyAgent, ExecutionTarget
    from odessy.runtime.types import ToolBatch, ToolCall
    from odessy.authority.policy import OdessyPolicy
    from odessy.tools.mcp_server import build_odessy_mcp_server
    capable = Scripted(ToolBatch((ToolCall("t", ToolRequest("time.now")),)), FinalResponse("done"))
    model = make_router(Scripted(), capable, Scripted())
    agent = OdessyAgent(model=model, policy=OdessyPolicy(workspace_root=tmp_path),
                        mcp_server=ExecutionTarget(build_odessy_mcp_server(workspace_root=tmp_path)))
    result = await agent.run_turn("time?")
    assert result.final_text == "done" and len(result.tool_observations) == 1
    assert result.metrics.routing["selected_role"] == "capable"
    assert result.metrics.model_call_count == 2


def test_transport_repairs_share_budget_and_no_cloud_after_exhaustion(monkeypatch):
    from odessy.models.lfm_model import LlamaCppLFMModel
    from odessy.models.generation_control import before_generation
    calls = []
    def chat(self, messages, tools=None):
        before_generation()
        calls.append(1)
        return {"message": {"content": "", "tool_calls": [{"function": {"name": "missing", "arguments": {}}}]}}
    monkeypatch.setattr(LlamaCppLFMModel, "_chat", chat)
    cloud = Scripted(FinalResponse("bad"))
    model = make_router(Scripted(ValueError("fast failed")), LlamaCppLFMModel(), cloud)
    model.begin_turn(max_model_calls=2)
    assert "budget" in model.propose("hi").text.lower()
    assert len(calls) == 1 and cloud.calls == 0
    assert model.last_turn_diagnostics.model_call_count == 2


def test_keep_alive_only_changes_explicitly_configured_payload(monkeypatch):
    import urllib.request
    captured = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def __iter__(self):
            return iter([b'{"message":{"content":"hello"},"done":true,"load_duration":1000000}'])
    def urlopen(request, **kwargs):
        captured.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    OllamaModel(text_only=True, keep_alive=0).propose("hi")
    OllamaModel(text_only=True).propose("hi")
    assert captured[0]["keep_alive"] == 0 and "tools" not in captured[0]
    assert "keep_alive" not in captured[1]


def test_context_reaches_capable_and_its_followups(monkeypatch):
    from odessy.models.lfm_model import LlamaCppLFMModel
    seen = []
    def chat(self, messages, tools=None):
        split = 1 if messages and messages[0].get("role") == "system" else 0
        seen.append([*messages[:split], *self.conversation_context, *messages[split:]])
        return {"message": {"content": "done"}}
    monkeypatch.setattr(LlamaCppLFMModel, "_chat", chat)
    model = make_router(Scripted(), LlamaCppLFMModel())
    context = ({"role": "user", "content": "previous"}, {"role": "assistant", "content": "answer"})
    model.begin_turn(conversation_context=context)
    model.propose("hello")
    model.respond_to_observation("hello", ToolObservation(ToolRequest("time.now"), {"status": "ok"}))
    assert all(context[0] in messages and context[1] in messages for messages in seen)


def test_cloud_admission_and_disclosure(monkeypatch, capsys):
    from odessy.cli import _announce_cloud
    model = make_router(Scripted(), Scripted(), Scripted())
    assert router_module().model_uses_cloud(model)
    _announce_cloud(model)
    assert "fallback enabled" in capsys.readouterr().err


@pytest.mark.parametrize("message", [
    {"content": '{"type":"tool","name":"terminal.run","arguments":{}}'},
    {"content": []},
])
def test_fast_rejects_structured_or_malformed_text(monkeypatch, message):
    monkeypatch.setattr(OllamaModel, "_chat", lambda *args, **kwargs: {"message": message})
    with pytest.raises(ValueError):
        OllamaModel(text_only=True).propose("hi")


def test_stop_between_begin_and_propose_is_preserved():
    model = make_router(Scripted(FinalResponse("bad")), Scripted())
    model.begin_turn()
    model.request_stop()
    assert "stopped" in model.propose("hi").text.lower()


def test_load_time_and_tokens_aggregate_across_escalations(monkeypatch):
    from odessy.models.lfm_model import LlamaCppLFMModel
    import urllib.request
    class Response:
        def __init__(self, output): self.output = output
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def __iter__(self): return iter([json.dumps(self.output).encode()])
    outputs = iter([
        {"message": {"content": ""}, "done": True, "load_duration": 2000000, "prompt_eval_count": 3, "eval_count": 2},
        {"message": {"content": "done"}, "done": True, "load_duration": 4000000, "prompt_eval_count": 4, "eval_count": 5},
    ])
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: Response(next(outputs)))
    model = make_router(OllamaModel(text_only=True), LlamaCppLFMModel())
    assert model.propose("hi").text == "done"
    diagnostic = model.last_turn_diagnostics
    assert diagnostic.prompt_tokens == 7 and diagnostic.completion_tokens == 7
    assert diagnostic.model_load_duration_ms == 6 and diagnostic.model_call_count == 2


def test_eval_auto_preserves_settings_and_route_attempt(monkeypatch, tmp_path):
    from evals import cli
    from odessy.runtime.types import FinalResponse
    model = make_router(Scripted(), Scripted(ToolRequest("time.now"), FinalResponse("2:30 PM")))
    monkeypatch.setattr("odessy.cli._build_model", lambda *a, **kw: model)
    output = tmp_path / "run"
    assert cli.main(["run", "--backend", "auto", "--case", "tools-time", "--repeat", "1", "--output-dir", str(output)]) == 0
    metadata = json.loads((output / "metadata.json").read_text())
    attempt = json.loads((output / "attempts.jsonl").read_text().splitlines()[0])
    assert metadata["model"] == "auto" and metadata["model_routing"]["cloud_fallback"] is False
    assert attempt["routing"]["selected_role"] == "capable"
    assert attempt["model_calls"] == 2
    assert "model_routing" in (output / "summary.md").read_text()


def test_cloud_retry_checks_stop_before_another_transport():
    from odessy.models.cloud_model import CloudModel, _TransientCloudError
    class Transient(CloudModel):
        def _post_chat_once(self, payload):
            self.transports += 1
            raise _TransientCloudError("busy", None)
    stop = asyncio.Event()
    cloud = Transient("deepseek", "test", "not-a-key", _sleep=lambda seconds: stop.set())
    cloud.transports = 0
    model = make_router(Scripted(), Scripted(ValueError("failed")), cloud)
    model.begin_turn(stop_event=stop)
    assert "stopped" in model.propose("run pwd").text.lower()
    assert cloud.transports == 1


@pytest.mark.anyio
async def test_repeated_cancellation_waits_for_generation(tmp_path):
    import threading
    from odessy.runtime.agent_loop import OdessyAgent
    from odessy.authority.policy import OdessyPolicy
    started, release = threading.Event(), threading.Event()
    class Blocking(Scripted):
        def propose(self, text):
            started.set()
            assert release.wait(3)
            return FinalResponse("late")
    model = make_router(Scripted(), Blocking())
    agent = OdessyAgent(model=model, policy=OdessyPolicy(workspace_root=tmp_path), mcp_server=None)
    task = asyncio.create_task(agent.run_turn("question"))
    try:
        while not started.is_set(): await asyncio.sleep(.001)
        task.cancel()
        await asyncio.sleep(.01)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done()
        with pytest.raises(RuntimeError, match="active"):
            model.begin_turn()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_routed_claude_disables_opaque_sdk_retries(monkeypatch):
    from types import SimpleNamespace
    from odessy.models import cloud_model
    options = []
    class SDK:
        class APIStatusError(Exception): pass
        class APIConnectionError(Exception): pass
        @staticmethod
        def Anthropic(**kwargs):
            options.append(kwargs)
            return SimpleNamespace(messages=SimpleNamespace(create=lambda **params: SimpleNamespace(
                content=[SimpleNamespace(type="text", text="done")], usage=None)))
    monkeypatch.setattr(cloud_model, "_import_anthropic", lambda: SDK)
    cloud = cloud_model.CloudModel("claude", "test", "not-a-key", retries=3)
    model = router_module().RoutedModel(router_module().RoutingConfig(cloud_fallback=True, cloud_backend="claude"),
            capable_factory=lambda: Scripted(ValueError("failed")), cloud_factory=lambda: cloud)
    assert model.propose("question").text == "done"
    assert options[0]["max_retries"] == 0


@pytest.mark.anyio
async def test_stop_after_confirmation_does_not_dispatch(tmp_path):
    from odessy.runtime.agent_loop import OdessyAgent, ExecutionTarget
    from odessy.authority.policy import OdessyPolicy
    from odessy.tools.mcp_server import build_odessy_mcp_server
    stop = asyncio.Event()
    def confirm(*args):
        stop.set()
        return True
    model = make_router(Scripted(), Scripted(ToolRequest("terminal.run", {"command": "python helper.py"})))
    agent = OdessyAgent(model=model, policy=OdessyPolicy(workspace_root=tmp_path), confirm=confirm,
                        mcp_server=ExecutionTarget(build_odessy_mcp_server(workspace_root=tmp_path)))
    result = await agent.run_turn("run helper", stop_event=stop)
    assert not result.tool_observations
    assert not any(entry.event == "mcp_tool_called" for entry in result.log)
