"""Unified context-budget coordination layer: estimator, ledger, guard, config."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from odessy.context_budget import (
    BudgetRegistry,
    ContextBudgetExceeded,
    ContextBudgetLedger,
    estimate_messages,
    estimate_tokens,
    current_context_budget,
)
from odessy.context_budget import registry as registry_mod


# -- estimator ----------------------------------------------------------------

def test_estimate_tokens_is_conservative_and_biased_high():
    assert estimate_tokens('') == 0
    assert estimate_tokens('x' * 30, chars_per_token=3) == 10
    # Round up, never down.
    assert estimate_tokens('x' * 10, chars_per_token=3) == 4
    # A bad ratio cannot divide by zero or under-count.
    assert estimate_tokens('x' * 10, chars_per_token=0) == 10
    # Non-strings are ignored.
    assert estimate_tokens(None) == 0  # type: ignore[arg-type]


def test_estimate_messages_adds_overhead_and_reads_text_blocks():
    msgs = [
        {'role': 'user', 'content': 'abc'},
        {'role': 'assistant', 'content': [{'type': 'text', 'text': 'de'}, {'type': 'tool_result', 'content': 'fghi'}]},
    ]
    assert estimate_messages(msgs, chars_per_token=3) > estimate_messages(msgs[:1], chars_per_token=3)
    assert estimate_messages([], chars_per_token=3) == 0


# -- registry -----------------------------------------------------------------

def test_registry_defaults_are_off_and_mirror_producers():
    r = BudgetRegistry.from_env({})
    assert r.enabled is False
    assert r.window_tokens == 0
    assert r.cloud_context_chars == 24000
    assert r.cloud_result_chars == 16000
    assert r.keep_recent_results == 2
    assert r.prepared_context_chars == 24000
    assert r.memory_chars == 8000
    assert r.memory_exchanges == 6
    assert r.local_num_ctx == 8192


def test_defaults_stay_in_sync_with_producers():
    from odessy.models import cloud_model
    from odessy.application.context import service as ctx_service
    from odessy.runtime import memory as rt_memory
    from odessy.models import ollama_model

    d = BudgetRegistry()
    assert d.cloud_context_chars == cloud_model.DEFAULT_CLOUD_CONTEXT_CHARS
    assert d.cloud_result_chars == cloud_model.DEFAULT_CLOUD_RESULT_CHARS
    assert d.keep_recent_results == cloud_model.DEFAULT_KEEP_RECENT_RESULTS
    assert d.prepared_context_chars == ctx_service.DEFAULT_BUDGET
    assert d.memory_chars == rt_memory.DEFAULT_MAX_CHARS
    assert d.memory_exchanges == rt_memory.DEFAULT_MAX_EXCHANGES
    assert d.local_num_ctx == ollama_model.DEFAULT_NUM_CTX


def test_registry_reads_guard_flag_and_window():
    r = BudgetRegistry.from_env({
        'ODESSY_CONTEXT_BUDGET_GUARD': 'on',
        'ODESSY_CONTEXT_WINDOW_TOKENS': '16000',
        'ODESSY_CONTEXT_CHARS_PER_TOKEN': '2',
        'ODESSY_CLOUD_CONTEXT_CHARS': '0',
    })
    assert r.enabled is True
    assert r.window_tokens == 16000
    assert r.chars_per_token == 2
    assert r.cloud_context_chars == 0
    # off values
    off = BudgetRegistry.from_env({'ODESSY_CONTEXT_BUDGET_GUARD': 'off'})
    assert off.enabled is False


# -- ledger -------------------------------------------------------------------

def test_ledger_is_noop_when_disabled():
    ledger = ContextBudgetLedger()  # enabled=False
    ledger.start_turn()
    ledger.record('prepared_context', 999999)
    ledger.record_chars('memory', 500000)
    ledger.record_messages('cloud', [{'role': 'user', 'content': 'x' * 500000}])
    ledger.check()
    ledger.check_messages([{'role': 'user', 'content': 'x' * 500000}])
    assert ledger.total() == 0
    assert ledger.snapshot() == {}


def test_record_replaces_per_source_and_check_uses_sums():
    ledger = ContextBudgetLedger(registry=BudgetRegistry(enabled=True, window_tokens=20))
    ledger.start_turn()
    ledger.record('prepared_context', 12)
    ledger.record('prepared_context', 6)  # replaces, not adds
    ledger.record('memory', 5)
    assert ledger.snapshot() == {'prepared_context': 6, 'memory': 5}
    assert ledger.total() == 11
    ledger.check()  # within window
    ledger.record('memory', 20)
    with pytest.raises(ContextBudgetExceeded, match='exceeds the 20-token window'):
        ledger.check()


def test_start_turn_resets_contributors():
    ledger = ContextBudgetLedger(registry=BudgetRegistry(enabled=True, window_tokens=100))
    ledger.record('x', 50)
    assert ledger.total() == 50
    ledger.start_turn()
    assert ledger.total() == 0


def test_check_messages_guards_whole_request_without_double_count():
    ledger = ContextBudgetLedger(registry=BudgetRegistry(enabled=True, window_tokens=10, chars_per_token=3))
    msgs = [{'role': 'user', 'content': 'y' * 40}]  # 14 tokens + 4 overhead = 18
    with pytest.raises(ContextBudgetExceeded):
        ledger.check_messages(msgs)
    # Does not mutate the messages.
    assert msgs[0]['content'] == 'y' * 40
    # A window of 0 never refuses even when enabled.
    ledger0 = ContextBudgetLedger(registry=BudgetRegistry(enabled=True, window_tokens=0, chars_per_token=3))
    ledger0.check_messages(msgs)


def test_bad_record_values_are_ignored():
    ledger = ContextBudgetLedger(registry=BudgetRegistry(enabled=True, window_tokens=10))
    ledger.record('', 5)
    ledger.record('x', 'not-an-int')
    ledger.record_chars('y', 'not-an-int')
    assert ledger.total() == 0


def test_parallel_session_turns_keep_their_own_counts(monkeypatch):
    from odessy.application.session import SessionController

    monkeypatch.setenv('ODESSY_CONTEXT_BUDGET_GUARD', 'on')

    async def exercise():
        first_recorded = asyncio.Event()
        second_recorded = asyncio.Event()

        async def first(text, **kwargs):
            budget = current_context_budget()
            await asyncio.to_thread(budget.record, 'prepared_context', 20)
            first_recorded.set()
            await second_recorded.wait()
            return budget.snapshot()

        async def second(text, **kwargs):
            budget = current_context_budget()
            await first_recorded.wait()
            await asyncio.to_thread(budget.record, 'prepared_context', 30)
            second_recorded.set()
            return budget.snapshot()

        one = SimpleNamespace(_run_turn_impl=first)
        two = SimpleNamespace(_run_turn_impl=second)
        one._run_turn_with_context = SessionController._run_turn_with_context.__get__(one)
        two._run_turn_with_context = SessionController._run_turn_with_context.__get__(two)
        results = await asyncio.gather(SessionController.run_turn(one, 'one'),
                                       SessionController.run_turn(two, 'two'))
        assert results == [{'prepared_context': 20}, {'prepared_context': 30}]
        assert current_context_budget() is None

    asyncio.run(exercise())


# -- config schema ------------------------------------------------------------

def test_new_settings_are_registered_and_validated():
    from odessy.config.schema import KNOWN_NAMES, validate_config

    for name in (
        'ODESSY_CLOUD_CONTEXT_CHARS', 'ODESSY_CLOUD_KEEP_RECENT_RESULTS',
        'ODESSY_CONTEXT_BUDGET_GUARD', 'ODESSY_CONTEXT_WINDOW_TOKENS',
        'ODESSY_CONTEXT_CHARS_PER_TOKEN',
    ):
        assert name in KNOWN_NAMES
    assert validate_config({'ODESSY_CLOUD_CONTEXT_CHARS': '-1'}).errors
    assert validate_config({'ODESSY_CONTEXT_CHARS_PER_TOKEN': '0'}).errors
    assert not validate_config({
        'ODESSY_CLOUD_CONTEXT_CHARS': '0',
        'ODESSY_CONTEXT_BUDGET_GUARD': 'on',
        'ODESSY_CONTEXT_WINDOW_TOKENS': '32000',
    }).errors


def test_defaults_mirror_matches_registry_constants():
    assert registry_mod.DEFAULT_CLOUD_CONTEXT_CHARS == 24000
    assert registry_mod.DEFAULT_WINDOW_TOKENS == 0


# -- send-point integration ---------------------------------------------------

def test_guard_refuses_send_when_messages_exceed_window(monkeypatch):
    """With the guard on, an oversized final message list sends no bytes."""
    import json
    from odessy.models.cloud_model import CloudModel
    from odessy.models.usage import UsageLedger

    class _OpenAI(CloudModel):
        def __init__(self, replies, **kwargs):
            super().__init__(provider="openai", model="m", api_key="k", usage=UsageLedger(), **kwargs)
            self.replies = list(replies)
            self.payloads = []

        def _post_chat_once(self, payload):
            self.payloads.append(json.loads(json.dumps(payload)))
            return self.replies.pop(0)

    answer = {"choices": [{"message": {"role": "assistant", "content": "done"}}], "usage": {}}
    # Turn the coordination guard on with a tiny window.
    monkeypatch.setenv('ODESSY_CONTEXT_BUDGET_GUARD', 'on')
    monkeypatch.setenv('ODESSY_CONTEXT_WINDOW_TOKENS', '5')

    model = _OpenAI([answer], context_chars=0)  # compaction off
    with pytest.raises(ContextBudgetExceeded):
        model.propose("x" * 200)
    assert model.payloads == []  # nothing was sent


def test_guard_off_does_not_change_send_behaviour(monkeypatch):
    """Default (off) guard leaves the existing request path untouched."""
    import json
    from odessy.models.cloud_model import CloudModel
    from odessy.models.usage import UsageLedger

    class _OpenAI(CloudModel):
        def __init__(self, replies, **kwargs):
            super().__init__(provider="openai", model="m", api_key="k", usage=UsageLedger(), **kwargs)
            self.replies = list(replies)
            self.payloads = []

        def _post_chat_once(self, payload):
            self.payloads.append(json.loads(json.dumps(payload)))
            return self.replies.pop(0)

    answer = {"choices": [{"message": {"role": "assistant", "content": "done"}}], "usage": {}}
    monkeypatch.delenv('ODESSY_CONTEXT_BUDGET_GUARD', raising=False)
    model = _OpenAI([answer], context_chars=0)
    model.propose("x" * 200)
    assert len(model.payloads) == 1  # sent normally


@pytest.mark.parametrize('provider', ['openai', 'claude'])
@pytest.mark.parametrize('large_field', ['system', 'tools', 'tool_call'])
def test_guard_counts_final_provider_input(monkeypatch, provider, large_field):
    import json

    from odessy.models import cloud_model
    from odessy.models.usage import UsageLedger

    large = 'x' * 900
    system = large if large_field == 'system' else 'Short system prompt'
    tools = [{'description': large}] if large_field == 'tools' else []
    monkeypatch.setattr(cloud_model, 'system_prompt', lambda: system)
    monkeypatch.setattr(cloud_model, f'cloud_tools_{"anthropic" if provider == "claude" else "openai"}',
                        lambda: tools)
    monkeypatch.setenv('ODESSY_CONTEXT_BUDGET_GUARD', 'on')
    monkeypatch.setenv('ODESSY_CONTEXT_WINDOW_TOKENS', '170')
    model = cloud_model.CloudModel(provider=provider, model='m', api_key='test', usage=UsageLedger(),
                                   context_chars=0, prompt_cache=False)
    messages = [{'role': 'user', 'content': 'A short request'}]
    if large_field == 'tool_call':
        if provider == 'claude':
            def dump_block(*, mode, exclude_none):
                return {'type': 'tool_use', 'id': 'call1', 'name': 'test_tool', 'input': {'data': large}}

            block = SimpleNamespace(model_dump=dump_block)
            messages.append({'role': 'assistant', 'content': [block]})
        else:
            call = {'id': 'call1', 'type': 'function',
                    'function': {'name': 'test_tool', 'arguments': json.dumps({'data': large})}}
            messages.append({'role': 'assistant', 'content': None, 'tool_calls': [call]})
    model._messages = messages
    exchange = model._exchange_anthropic if provider == 'claude' else model._exchange_openai
    sender = '_create_message' if provider == 'claude' else '_post_chat_once'
    sent = []

    def send(payload):
        sent.append(payload)
        if provider == 'claude':
            return SimpleNamespace(content=[], usage=None)
        return {'choices': [{'message': {'role': 'assistant', 'content': 'done'}}]}

    monkeypatch.setattr(model, sender, send)
    with pytest.raises(ContextBudgetExceeded, match='170-token window'):
        exchange()
    assert sent == []

    monkeypatch.setenv('ODESSY_CONTEXT_WINDOW_TOKENS', '2000')
    exchange()
    assert len(sent) == 1
    assert sent[0]['tools'] == tools
