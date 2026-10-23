"""Cloud cost and usage: prompt caching, cached-token accounting, the run ledger, the budget."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import odessy.cli as cli
from odessy.models.cloud_model import CloudModel, build_cloud_model
from odessy.models.fake_model import FakeModel
from odessy.models.usage import (
    CloudBudgetExceeded,
    CloudUsageConfigError,
    Pricing,
    UsageLedger,
    pricing_from_env,
    process_ledger,
)
from odessy.runtime.types import FinalResponse, ToolRequest


# --- prices and the ledger -----------------------------------------------------------


def test_no_prices_means_tokens_only() -> None:
    assert pricing_from_env({}) is None
    ledger = UsageLedger()
    ledger.record(prompt=1000, cached=0, completion=100)
    assert ledger.cost() is None
    assert "~" not in (ledger.turn_line() or "")


def test_prices_are_per_million_tokens_and_cached_input_is_cheaper() -> None:
    pricing = pricing_from_env(
        {"ODESSY_CLOUD_PRICE_INPUT": "2", "ODESSY_CLOUD_PRICE_CACHED_INPUT": "0.2", "ODESSY_CLOUD_PRICE_OUTPUT": "8",
         "ODESSY_CLOUD_PRICE_CURRENCY": "CNY"}
    )
    assert pricing == Pricing(input=2, cached_input=0.2, output=8, currency="CNY")
    # 600k uncached at 2, 400k cached at 0.2, 100k out at 8 = 1.2 + 0.08 + 0.8
    assert pricing.cost(prompt=1_000_000, cached=400_000, completion=100_000) == pytest.approx(2.08)


def test_cached_price_defaults_to_the_input_price() -> None:
    pricing = pricing_from_env({"ODESSY_CLOUD_PRICE_INPUT": "1", "ODESSY_CLOUD_PRICE_OUTPUT": "2"})
    assert pricing is not None and pricing.cached_input == 1 and pricing.currency == "USD"


@pytest.mark.parametrize(
    ("env", "problem"),
    [
        ({"ODESSY_CLOUD_PRICE_INPUT": "1"}, "both"),
        ({"ODESSY_CLOUD_PRICE_INPUT": "x", "ODESSY_CLOUD_PRICE_OUTPUT": "1"}, "number"),
        ({"ODESSY_CLOUD_PRICE_INPUT": "-1", "ODESSY_CLOUD_PRICE_OUTPUT": "1"}, "negative"),
        ({"ODESSY_CLOUD_BUDGET_COST": "5"}, "needs prices"),
        ({"ODESSY_CLOUD_BUDGET_TOKENS": "lots"}, "number"),
    ],
)
def test_bad_settings_are_refused_with_the_reason(env: dict[str, str], problem: str) -> None:
    with pytest.raises(CloudUsageConfigError, match=problem):
        UsageLedger.from_env(env)


@pytest.mark.parametrize("setting", [
    "ODESSY_CLOUD_PRICE_INPUT", "ODESSY_CLOUD_PRICE_CACHED_INPUT",
    "ODESSY_CLOUD_PRICE_OUTPUT", "ODESSY_CLOUD_BUDGET_TOKENS", "ODESSY_CLOUD_BUDGET_COST",
])
@pytest.mark.parametrize("value", ["NaN", "inf", "-inf"])
def test_nonfinite_prices_and_budgets_are_rejected(setting: str, value: str) -> None:
    env = {"ODESSY_CLOUD_PRICE_INPUT": "1", "ODESSY_CLOUD_PRICE_OUTPUT": "1", setting: value}
    with pytest.raises(CloudUsageConfigError, match="finite"):
        UsageLedger.from_env(env)


def test_the_ledger_keeps_a_turn_and_a_run_total() -> None:
    ledger = UsageLedger(pricing=Pricing(input=1, cached_input=0.1, output=2))
    ledger.record(prompt=2000, cached=1500, completion=100)
    ledger.record(prompt=2500, cached=2000, completion=50)
    line = ledger.turn_line()
    assert line is not None and "4,500 in (3,500 cached), 150 out" in line and "2 call(s)" in line
    assert "run" not in line  # the run so far is this turn
    ledger.start_turn()
    assert ledger.turn_line() is None
    ledger.record(prompt=100, cached=0, completion=10)
    assert "run 4,600 in" in (ledger.turn_line() or "")
    assert "3 call(s)" in (ledger.session_line() or "")


def test_the_token_budget_stops_further_requests() -> None:
    warnings: list[str] = []
    ledger = UsageLedger(budget_tokens=1000, warn=warnings.append)
    ledger.check()
    ledger.record(prompt=700, cached=0, completion=100)  # 80%
    assert len(warnings) == 1 and "80%" in warnings[0]
    ledger.record(prompt=50, cached=0, completion=10)
    assert len(warnings) == 1  # once
    ledger.check()
    ledger.record(prompt=200, cached=0, completion=0)
    with pytest.raises(CloudBudgetExceeded, match="1,060 of 1,000 tokens"):
        ledger.check()


def test_the_cost_budget_uses_the_configured_prices() -> None:
    ledger = UsageLedger(pricing=Pricing(input=10, cached_input=1, output=10), budget_cost=0.01)
    ledger.record(prompt=900, cached=0, completion=0)
    ledger.check()
    ledger.record(prompt=200, cached=0, completion=0)
    with pytest.raises(CloudBudgetExceeded, match="0.0110 of 0.01 USD"):
        ledger.check()


def test_the_process_ledger_reads_the_environment_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODESSY_CLOUD_BUDGET_TOKENS", "500")
    assert process_ledger().budget_tokens == 500
    assert process_ledger() is process_ledger()


# --- Claude: cache breakpoints and cached tokens ---------------------------------------


class _Claude(CloudModel):
    def __init__(self, responses: list[Any], **kwargs: Any) -> None:
        super().__init__(provider="claude", model="claude-opus-5-5", api_key="k", **kwargs)
        self.responses = list(responses)
        self.params: list[dict[str, Any]] = []

    def _create_message(self, params: dict[str, Any]) -> Any:
        self.params.append(params)
        return self.responses.pop(0)


def _claude(text: str = "", tool: tuple[str, str, dict] | None = None, **usage: int) -> Any:
    content = [SimpleNamespace(type="text", text=text)] if text else []
    if tool:
        content.append(SimpleNamespace(type="tool_use", id=tool[0], name=tool[1], input=tool[2]))
    return SimpleNamespace(
        content=content,
        stop_reason="tool_use" if tool else "end_turn",
        usage=SimpleNamespace(**{"input_tokens": 10, "output_tokens": 5, **usage}),
    )


def test_claude_marks_the_tools_and_system_prompt_cacheable() -> None:
    model = _Claude([_claude("hi")], usage=UsageLedger())
    model.propose("hello")
    (params,) = model.params
    assert params["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert all("cache_control" not in tool for tool in params["tools"][:-1])
    (system,) = params["system"]
    assert system["type"] == "text" and system["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in str(params["messages"])


def test_claude_caching_can_be_turned_off() -> None:
    model = _Claude([_claude("hi")], usage=UsageLedger(), prompt_cache=False)
    model.propose("hello")
    assert isinstance(model.params[0]["system"], str)
    assert "cache_control" not in str(model.params[0]["tools"])


def test_claude_usage_counts_cache_writes_and_reads_as_prompt_tokens() -> None:
    ledger = UsageLedger()
    model = _Claude(
        [
            _claude(tool=("t1", "time__now", {}), input_tokens=20, cache_creation_input_tokens=3000, output_tokens=8),
            _claude("It is noon.", input_tokens=40, cache_read_input_tokens=3000, output_tokens=6),
        ],
        usage=ledger,
    )
    action = model.propose("time?")
    assert isinstance(action, ToolRequest)
    from odessy.runtime.types import ToolObservation

    model.respond_to_observation("time?", ToolObservation(request=action, result={"status": "ok", "iso_time": "12:00"}))
    diagnostics = model.last_turn_diagnostics
    assert diagnostics.prompt_tokens == 3020 + 3040
    assert diagnostics.cached_prompt_tokens == 3000
    assert diagnostics.completion_tokens == 14
    assert (ledger.total.prompt, ledger.total.cached, ledger.total.completion, ledger.total.calls) == (6060, 3000, 14, 2)


# --- OpenAI-compatible: cached tokens as each provider reports them --------------------


class _OpenAI(CloudModel):
    def __init__(self, provider: str, replies: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(provider=provider, model="m", api_key="k", **kwargs)
        self.replies = list(replies)
        self.requests = 0

    def _post_chat_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests += 1
        return self.replies.pop(0)


def _reply(text: str, usage: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": text}}], "usage": usage}


@pytest.mark.parametrize(
    ("provider", "usage"),
    [
        ("openai", {"prompt_tokens": 1000, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 768}}),
        ("qwen", {"prompt_tokens": 1000, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 768}}),
        ("deepseek", {"prompt_tokens": 1000, "completion_tokens": 20, "prompt_cache_hit_tokens": 768, "prompt_cache_miss_tokens": 232}),
        ("kimi", {"prompt_tokens": 1000, "completion_tokens": 20, "cached_tokens": 768}),
    ],
)
def test_cached_tokens_are_read_from_each_providers_usage(provider: str, usage: dict[str, Any]) -> None:
    ledger = UsageLedger()
    model = _OpenAI(provider, [_reply("ok", usage)], usage=ledger)
    assert model.propose("hi") == FinalResponse(text="ok")
    assert model.last_turn_diagnostics.cached_prompt_tokens == 768
    assert (ledger.total.prompt, ledger.total.cached, ledger.total.completion) == (1000, 768, 20)


def test_a_provider_without_cache_figures_records_none_cached() -> None:
    ledger = UsageLedger()
    model = _OpenAI("deepseek", [_reply("ok", {"prompt_tokens": 50, "completion_tokens": 5})], usage=ledger)
    model.propose("hi")
    assert model.last_turn_diagnostics.cached_prompt_tokens is None
    assert ledger.total.cached == 0


def test_a_spent_budget_sends_no_further_request() -> None:
    ledger = UsageLedger(budget_tokens=100, warn=lambda message: None)
    model = _OpenAI(
        "deepseek",
        [{"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "time__now", "arguments": "{}"}}]}}],
          "usage": {"prompt_tokens": 150, "completion_tokens": 5}}],
        usage=ledger,
    )
    action = model.propose("time?")
    from odessy.runtime.types import ToolObservation

    with pytest.raises(CloudBudgetExceeded):
        model.respond_to_observation("time?", ToolObservation(request=action, result={"status": "ok"}))
    assert model.requests == 1


def test_the_prompt_cache_setting_comes_from_the_environment() -> None:
    off = build_cloud_model("deepseek", {"DEEPSEEK_API_KEY": "k", "ODESSY_CLOUD_PROMPT_CACHE": "off"})
    on = build_cloud_model("deepseek", {"DEEPSEEK_API_KEY": "k"})
    assert (off.prompt_cache, on.prompt_cache) == (False, True)


def test_adapters_share_the_process_ledger_by_default() -> None:
    first = _OpenAI("deepseek", [_reply("a", {"prompt_tokens": 10, "completion_tokens": 1})])
    second = _OpenAI("qwen", [_reply("b", {"prompt_tokens": 20, "completion_tokens": 2})])
    first.propose("x")
    second.propose("y")
    assert process_ledger().total.prompt == 30


# --- the CLI ---------------------------------------------------------------------------


def test_the_cli_prints_a_turns_cloud_usage(capsys: pytest.CaptureFixture[str]) -> None:
    with cli._usage_report():
        process_ledger().record(prompt=1200, cached=1000, completion=40)
    assert "cloud usage: this turn 1,200 in (1,000 cached), 40 out" in capsys.readouterr().err


def test_the_cli_prints_nothing_for_a_turn_without_cloud_calls(capsys: pytest.CaptureFixture[str]) -> None:
    with cli._usage_report():
        pass
    assert "cloud usage" not in capsys.readouterr().err


def test_a_bad_price_setting_stops_a_cloud_run_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODESSY_CLOUD_PRICE_INPUT", "1")
    model = CloudModel(provider="deepseek", model="m", api_key="k")
    with pytest.raises(SystemExit, match="both"):
        cli._announce_model(model)


def test_a_local_run_ignores_cloud_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODESSY_CLOUD_PRICE_INPUT", "1")
    cli._announce_model(FakeModel())
    with cli._usage_report():
        pass


def test_the_budget_is_announced_at_startup(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("ODESSY_CLOUD_BUDGET_TOKENS", "200000")
    cli._announce_model(CloudModel(provider="deepseek", model="m", api_key="k"))
    assert "cloud budget for this run: 0 of 200,000 tokens" in capsys.readouterr().err


# --- doctor ----------------------------------------------------------------------------


def test_doctor_shows_the_cloud_usage_settings() -> None:
    from odessy.config.doctor import _cloud_usage_check

    plain = _cloud_usage_check({})
    assert plain.status == "ok" and "no prices set" in plain.summary and "no budget" in plain.summary
    priced = _cloud_usage_check(
        {"ODESSY_CLOUD_PRICE_INPUT": "2", "ODESSY_CLOUD_PRICE_OUTPUT": "8", "ODESSY_CLOUD_BUDGET_TOKENS": "100000"}
    )
    assert "2 / 2 cached / 8 out USD" in priced.summary and "limit 100,000 tokens" in priced.summary
    broken = _cloud_usage_check({"ODESSY_CLOUD_BUDGET_COST": "1"})
    assert broken.status == "fail" and "needs prices" in broken.summary
