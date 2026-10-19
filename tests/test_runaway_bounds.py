"""Bounds on a model call, and what a run does when the model server dies.

Long generations and unavailable model servers need distinct recorded outcomes.
These tests do not establish what caused the Jetson's allocation failures.

So these tests pin three things:

* a local call cannot generate without a limit;
* a call that ignores the token limit is stopped at a stream line boundary;
* a run stops when the server is gone, instead of grading 30 attempts against it.

They fake only ``urllib.request.urlopen`` and the agent, and never call a model.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from odessy.cloud_model import DEFAULT_OPENAI_MAX_TOKENS, CloudModel
from odessy.lfm_model import LlamaCppLFMModel
from odessy.ollama_model import (
    CALL_TIMEOUT_ENV,
    DEFAULT_NUM_CTX,
    DEFAULT_NUM_PREDICT,
    NUM_CTX_ENV,
    NUM_PREDICT_ENV,
    OllamaModel,
    _OllamaCallTimeout,
    _OllamaStreamTimeout,
)

from evals.definitions import load_cases
from evals.graders import grade
from evals.harness import Attempt, looks_like_model_unreachable


def _case(case_id: str):
    return next(case for case in load_cases() if case.id == case_id)


class _StreamResponse:
    """Just enough of an HTTP response to iterate NDJSON lines."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __enter__(self) -> "_StreamResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def __iter__(self):
        for line in self._lines:
            yield line


def _one_answer() -> list[bytes]:
    body = {"message": {"role": "assistant", "content": "ok"}, "done": True, "eval_count": 1}
    return [(json.dumps(body) + "\n").encode("utf-8")]


def _capture(monkeypatch: pytest.MonkeyPatch, lines: list[bytes]) -> dict[str, Any]:
    """Serve ``lines`` for every request and record each request payload."""

    captured: dict[str, Any] = {}

    def fake_urlopen(request, timeout=None):  # noqa: ANN001 - urlopen signature
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _StreamResponse(lines)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return captured


# -- the limit is always sent -------------------------------------------------


def test_ollama_requests_carry_a_generation_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch, _one_answer())

    OllamaModel(model="gemma3:1b")._chat([{"role": "user", "content": "hi"}])

    options = captured["payload"]["options"]
    assert options["num_predict"] == DEFAULT_NUM_PREDICT
    assert options["num_ctx"] == DEFAULT_NUM_CTX
    assert options["temperature"] == 0.0


def test_lfm_requests_carry_a_generation_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch, _one_answer())

    LlamaCppLFMModel()._chat([{"role": "user", "content": "hi"}])

    assert captured["payload"]["options"]["num_predict"] == DEFAULT_NUM_PREDICT
    assert captured["payload"]["options"]["num_ctx"] == DEFAULT_NUM_CTX


def test_lfm_temperature_is_inside_options_where_ollama_reads_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A top-level ``temperature`` is not part of Ollama's API and was ignored.

    The adapter has always defaulted to 0.0, but the field sat outside ``options``,
    so the model sampled at Ollama's default instead. That is a plausible contributor
    to the runaways: a 1.2B model at its default sampling temperature rambles.
    """

    captured = _capture(monkeypatch, _one_answer())

    LlamaCppLFMModel(temperature=0.0)._chat([{"role": "user", "content": "hi"}])

    payload = captured["payload"]
    assert "temperature" not in payload
    assert payload["options"]["temperature"] == 0.0


def test_the_limits_are_configurable_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(NUM_PREDICT_ENV, "256")
    monkeypatch.setenv(NUM_CTX_ENV, "2048")
    monkeypatch.setenv(CALL_TIMEOUT_ENV, "45")

    model = LlamaCppLFMModel()

    assert (model.num_predict, model.num_ctx, model.call_timeout) == (256, 2048, 45.0)


# -- a call that ignores the limit is cut off ---------------------------------


def test_a_generation_that_never_ends_is_stopped_by_the_total_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endless = [(json.dumps({"message": {"content": "x"}, "done": False}) + "\n").encode("utf-8")] * 500
    _capture(monkeypatch, endless)

    # The clock is faked: on Windows ``monotonic`` advances in ~15 ms steps, and 500
    # in-memory lines finish inside one tick, so a real clock makes this a race. One
    # second per line means the budget is exceeded on a known line.
    ticks = iter(range(10_000))
    monkeypatch.setattr("odessy.ollama_model.time.monotonic", lambda: next(ticks))

    # num_predict is the first line of defence; this proves the second, for a server
    # that ignores it or a stream that ignores the model's stop token.
    model = LlamaCppLFMModel(num_predict=0, call_timeout=5.0)

    with pytest.raises(_OllamaCallTimeout) as raised:
        model._chat([{"role": "user", "content": "hi"}])

    assert CALL_TIMEOUT_ENV in str(raised.value)


@pytest.mark.anyio
@pytest.mark.parametrize("error_type", [_OllamaCallTimeout, _OllamaStreamTimeout])
async def test_model_generation_timeout_is_recorded_distinctly(error_type) -> None:
    from evals.harness import run_attempt

    class _TimedOutModel:
        def propose(self, user_text: str) -> None:
            raise error_type("model call exceeded 300s of generation")

    record = await run_attempt(_case("tools-time"), _TimedOutModel(), 1)
    assert record.outcome == "timeout"
    assert record.error == "model call exceeded 300s of generation"


def test_documented_limit_settings_can_be_uncommented(tmp_path: Path) -> None:
    from odessy.config_file import read_env_file

    names = (NUM_PREDICT_ENV, NUM_CTX_ENV, CALL_TIMEOUT_ENV, "ODESSY_CLOUD_OPENAI_MAX_TOKENS")
    config = Path(__file__).parents[1] / "config" / "odessy.env"
    examples = [line[2:] for line in config.read_text(encoding="utf-8").splitlines()
                if any(line.startswith(f"# {name}=") for name in names)]
    path = tmp_path / "limits.env"
    path.write_text("\n".join(examples), encoding="utf-8")
    values = read_env_file(path)
    assert set(values) == set(names)
    assert all(int(value) > 0 for value in values.values())


def test_summary_keeps_timeout_and_completion_rates_visible() -> None:
    from evals.report import render_markdown, summarize

    case = _case("tools-time")
    timeout = Attempt(case_id=case.id, category=case.category, attempt=1, outcome="timeout")
    completed = Attempt(case_id=case.id, category=case.category, attempt=2, outcome="completed")
    records = [
        {**timeout.to_dict(), "grade": grade(case, timeout).to_dict()},
        {**completed.to_dict(), "grade": {"passed": True, "excluded": False, "checks": {}}},
    ]
    summary = summarize(records)
    assert summary["pass_rate"] == 1.0
    assert summary["timeout_rate"] == 0.5
    assert summary["completion_rate"] == 0.5
    assert summary["pass_rate_all_attempts"] == 0.5
    markdown = render_markdown(summary, {})
    assert "Timeouts: 1/2 (50.0%)" in markdown
    assert "Passed / all recorded attempts: 50.0%" in markdown


# -- timeouts are excluded from pass rates ------------------------------------


def test_a_timed_out_attempt_is_excluded_rather_than_graded() -> None:
    case = _case("tools-time")
    attempt = Attempt(
        case_id=case.id,
        category=case.category,
        attempt=1,
        outcome="timeout",
        error="attempt exceeded 1800s after 1 proposal(s)",
    )

    result = grade(case, attempt)

    assert result.excluded
    assert not result.passed
    assert result.checks["reason"] == "attempt timed out"


@pytest.mark.anyio
async def test_an_attempt_that_overruns_its_budget_is_recorded_as_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from evals import harness

    class _HangingAgent:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def run_turn(self, prompt: str) -> None:
            await asyncio.sleep(5)

    monkeypatch.setattr(harness, "OdessyAgent", _HangingAgent)

    record = await harness.run_attempt(
        _case("tools-time"), None, 1, timeout_seconds=0.05
    )

    assert record.outcome == "timeout"
    assert "exceeded" in (record.error or "")


# -- a dead model server ends the run ----------------------------------------


def test_a_refused_connection_means_the_server_is_gone() -> None:
    attempt = Attempt(
        case_id="tools-time",
        category="tools",
        attempt=1,
        outcome="infra_error",
        error=(
            "Ollama LFM API call failed: <urlopen error [Errno 111] "
            "Connection refused>"
        ),
    )

    assert looks_like_model_unreachable(attempt)


def test_a_model_answer_is_not_mistaken_for_an_unreachable_server() -> None:
    blocked = Attempt(
        case_id="tools-time",
        category="tools",
        attempt=1,
        outcome="blocked",
        error="Policy requires confirmation before execution: terminal.run",
    )
    invalid = Attempt(
        case_id="tools-time",
        category="tools",
        attempt=1,
        outcome="infra_error",
        error="Model repair failed after one retry",
    )

    assert not looks_like_model_unreachable(blocked)
    assert not looks_like_model_unreachable(invalid)


def test_a_run_aborts_when_the_model_server_stops_answering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The 2026-10-18 run kept going for 33 attempts after Ollama died."""

    from evals import cli

    class _DownModel:
        def propose(self, user_text: str) -> None:
            raise RuntimeError(
                "Ollama LFM API call failed: <urlopen error [Errno 111] Connection refused>"
            )

        def respond_to_observation(self, user_text: str, observation: object) -> None:
            raise AssertionError("no observation should reach a dead model")

    monkeypatch.setattr("odessy.cli._build_model", lambda *a, **k: _DownModel())
    run_dir = tmp_path / "run"

    exit_code = cli.main(
        [
            "run",
            "--backend",
            "down",
            "--case",
            "tools-time",
            "--repeat",
            "5",
            "--max-infra-errors",
            "3",
            "--output-dir",
            str(run_dir),
        ]
    )

    assert exit_code == 2
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["aborted"]["attempts_completed"] == 3
    assert "Connection refused" in metadata["aborted"]["reason"]
    assert "Run aborted after 3 attempts" in (run_dir / "summary.md").read_text(encoding="utf-8")
    # The remaining attempts were never made, so there is nothing to misread.
    written = (run_dir / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(written) == 3


# -- cloud parity -------------------------------------------------------------


def test_openai_compatible_requests_send_an_output_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claude was the only provider with a ceiling; the others had none at all."""

    model = CloudModel(provider="deepseek", model="deepseek-flash", api_key="test-key")
    captured: dict[str, Any] = {}

    def fake_post(payload: dict[str, Any]) -> dict[str, Any]:
        captured["payload"] = payload
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(model, "_post_chat", fake_post)

    model._exchange_openai()

    assert captured["payload"]["max_tokens"] == DEFAULT_OPENAI_MAX_TOKENS


def test_a_claude_max_tokens_setting_does_not_leak_into_openai_calls() -> None:
    """16000 is above several providers' per-model limits, where it is a 400.

    Anthropic truncates at ``max_tokens``; OpenAI-compatible providers reject a value
    larger than the model allows, so the two ceilings are separate settings.
    """

    model = CloudModel(
        provider="deepseek", model="deepseek-flash", api_key="k", max_tokens=16000
    )

    assert model.openai_max_tokens == DEFAULT_OPENAI_MAX_TOKENS
