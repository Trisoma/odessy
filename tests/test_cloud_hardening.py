"""Cloud adapter behaviour added when the cloud model became the main target."""

from __future__ import annotations

import io
import json
import urllib.error
from typing import Any

import pytest

import odessy.models.cloud_model as cloud
from odessy.cli import _build_agent, default_max_model_calls
from odessy.models.cloud_model import CloudModel, _TransientCloudError, build_cloud_model
from odessy.models.fake_model import FakeModel
from odessy.runtime.types import FinalResponse, ToolObservation, ToolRequest


def _reply(content: str = "", calls: list[tuple[str, str, dict[str, Any]]] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            for call_id, name, args in calls
        ]
    return {"choices": [{"message": message}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class _Flaky(CloudModel):
    """Fails the first ``failures`` requests transiently, then answers."""

    def __init__(self, failures: list[Exception], replies: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(provider="deepseek", model="m", api_key="k", **kwargs)
        self.failures = list(failures)
        self.replies = list(replies)
        self.sleeps: list[float] = []
        self.payloads: list[dict[str, Any]] = []
        self._sleep = self.sleeps.append

    def _post_chat_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(json.loads(json.dumps(payload)))
        if self.failures:
            raise self.failures.pop(0)
        return self.replies.pop(0)


# --- retries -------------------------------------------------------------------------


def test_transient_failures_are_retried_with_backoff() -> None:
    model = _Flaky(
        [_TransientCloudError("429"), _TransientCloudError("503")],
        [_reply("It is noon.")],
    )
    assert model.propose("time?") == FinalResponse(text="It is noon.")
    assert model.sleeps == [1.0, 2.0]


def test_retry_after_is_honoured() -> None:
    model = _Flaky([_TransientCloudError("429", retry_after=7.0)], [_reply("ok then")])
    model.propose("hi")
    assert model.sleeps == [7.0]


def test_retries_are_bounded() -> None:
    model = _Flaky([_TransientCloudError("503")] * 3, [], retries=2)
    with pytest.raises(RuntimeError, match="503"):
        model.propose("hi")
    assert len(model.payloads) == 3


def _http_error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    import email.message

    message = email.message.Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError("https://x/chat/completions", code, "err", message, io.BytesIO(b'{"error":"x"}'))


def test_rate_limit_status_is_transient_and_reads_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(request: Any, timeout: float) -> Any:
        raise _http_error(429, {"Retry-After": "3"})

    monkeypatch.setattr(cloud.urllib.request, "build_opener", lambda *args: __import__('types').SimpleNamespace(open=fail))
    model = CloudModel(provider="deepseek", model="m", api_key="k")
    with pytest.raises(_TransientCloudError) as raised:
        model._post_chat_once({"model": "m"})
    assert raised.value.retry_after == 3.0


def test_a_bad_request_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def fail(request: Any, timeout: float) -> Any:
        calls.append(1)
        raise _http_error(400)

    monkeypatch.setattr(cloud.urllib.request, "build_opener", lambda *args: __import__('types').SimpleNamespace(open=fail))
    model = CloudModel(provider="deepseek", model="m", api_key="k")
    model._sleep = lambda seconds: None
    with pytest.raises(RuntimeError, match=r"\(400\)"):
        model._post_chat({"model": "m"})
    assert calls == [1]


def test_retry_and_result_settings_come_from_the_environment() -> None:
    model = build_cloud_model(
        "deepseek",
        {"DEEPSEEK_API_KEY": "k", "ODESSY_CLOUD_RETRIES": "0", "ODESSY_CLOUD_MAX_RESULT_CHARS": "500"},
    )
    assert model.retries == 0
    assert model.max_result_chars == 500
    default = build_cloud_model("deepseek", {"DEEPSEEK_API_KEY": "k"})
    assert default.retries == 2


# --- tool results ---------------------------------------------------------------------


def _after_one_call(model: _Flaky, result: dict[str, Any]) -> dict[str, Any]:
    request = model.propose("do it")
    assert isinstance(request, ToolRequest)
    model.respond_to_observation("do it", ToolObservation(request=request, result=result))
    return [m for m in model.payloads[-1]["messages"] if m["role"] == "tool"][-1]


def test_large_results_are_capped_before_sending() -> None:
    model = _Flaky([], [_reply(calls=[("c1", "filesystem__read", {"path": "big.txt"})]), _reply("Done.")],
                   max_result_chars=100)
    message = _after_one_call(model, {"content": "x" * 5000})
    assert len(message["content"]) < 200
    assert "more characters omitted by Odessy" in message["content"]


class _FakeClaudeBlock:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _Claude(CloudModel):
    def __init__(self, responses: list[Any]) -> None:
        super().__init__(provider="claude", model="claude-opus-5-5", api_key="k")
        self.responses = list(responses)
        self.params: list[dict[str, Any]] = []

    def _create_message(self, params: dict[str, Any]) -> Any:
        self.params.append(json.loads(json.dumps(params, default=lambda o: o.__dict__)))
        return self.responses.pop(0)


def _claude_response(blocks: list[Any], stop_reason: str = "end_turn") -> Any:
    return _FakeClaudeBlock(content=blocks, stop_reason=stop_reason, usage=_FakeClaudeBlock(input_tokens=1, output_tokens=1))


@pytest.mark.parametrize("status", ["rejected", "error"])
def test_failed_results_are_flagged_as_errors_to_claude(status: str) -> None:
    model = _Claude(
        [
            _claude_response([_FakeClaudeBlock(type="tool_use", id="t1", name="filesystem__read", input={"path": "x"})], "tool_use"),
            _claude_response([_FakeClaudeBlock(type="text", text="That file does not exist.")]),
        ]
    )
    request = model.propose("read x")
    model.respond_to_observation("read x", ToolObservation(request=request, result={"status": status, "error": "nope"}))
    block = model.params[1]["messages"][-1]["content"][0]
    assert block["type"] == "tool_result"
    assert block.get("is_error") is True


def test_successful_results_are_not_flagged() -> None:
    model = _Claude(
        [
            _claude_response([_FakeClaudeBlock(type="tool_use", id="t1", name="time__now", input={})], "tool_use"),
            _claude_response([_FakeClaudeBlock(type="text", text="Noon.")]),
        ]
    )
    request = model.propose("time")
    model.respond_to_observation("time", ToolObservation(request=request, result={"iso_time": "12:00"}))
    assert "is_error" not in model.params[1]["messages"][-1]["content"][0]


# --- model-call ceiling ---------------------------------------------------------------


def test_cloud_backends_get_a_higher_call_ceiling(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    assert default_max_model_calls(cloud=True) == 8
    assert default_max_model_calls(cloud=False) == 4
    cloud_model = CloudModel(provider="deepseek", model="m", api_key="k")
    assert _build_agent(tmp_path, cloud_model)._max_model_calls == 8
    assert _build_agent(tmp_path, FakeModel())._max_model_calls == 4
    monkeypatch.setenv("ODESSY_MAX_MODEL_CALLS", "5")
    assert _build_agent(tmp_path, cloud_model)._max_model_calls == 5


def test_eval_run_uses_the_backend_default_ceiling(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from evals.cli import main as evals_main

    model = _Flaky([], [_reply("It is noon.")] * 3)
    monkeypatch.setattr("odessy.cli._build_model", lambda *args, **kwargs: model)
    out = tmp_path / "run"
    evals_main(["run", "--backend", "deepseek", "--case", "tools-time", "--repeat", "1", "--output-dir", str(out)])
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["max_model_calls"] == 8
    assert "keyword_routing" not in metadata
