import pytest

from odessy.models.usage import CloudBudgetExceeded, CloudUsageConfigError, Pricing, UsageLedger, process_ledger


def test_profile_totals_use_each_price_and_global_budget():
    ledger = UsageLedger(budget_tokens=300, budget_cost=1)
    a = ledger.for_profile('a', 'openai', 'one', Pricing(1000, 1000, 1000))
    b = ledger.for_profile('b', 'other', 'two', Pricing(2000, 2000, 2000))
    a.record(prompt=100, cached=0, completion=0)
    b.record(prompt=100, cached=0, completion=0)
    assert ledger.cost() == pytest.approx(.3)
    assert ledger.total.tokens == 200 and ledger.turn.calls == 2
    assert ledger.profiles[('a', 'openai', 'one')]['cost'] == pytest.approx(.1)
    ledger.start_turn()
    assert ledger.profiles[('a', 'openai', 'one')]['turn'].tokens == 0
    a.record(prompt=100, cached=0, completion=0)
    with pytest.raises(CloudBudgetExceeded):
        b.check()


def test_unknown_price_cannot_bypass_cost_budget():
    ledger = UsageLedger(pricing=Pricing(1, 1, 1), budget_cost=1)
    unknown = ledger.for_profile('unknown', 'x', 'y')
    with pytest.raises(CloudUsageConfigError, match='price'):
        unknown.check()
    assert unknown.cost() is None


def test_profiles_reject_mixed_currency_and_return_snapshots():
    ledger = UsageLedger()
    a = ledger.for_profile('a', 'x', 'm', Pricing(1, 1, 1))
    a.record(prompt=5, cached=0, completion=1)
    ledger.profiles[('a', 'x', 'm')]['total'].prompt = 999
    assert ledger.total.prompt == 5
    with pytest.raises(CloudUsageConfigError, match='currenc'):
        ledger.for_profile('b', 'x', 'm', Pricing(1, 1, 1, 'CNY'))


def test_reporting_distinguishes_models_and_explicit_unknown_prices():
    ledger = UsageLedger()
    a = ledger.for_profile('same', 'p', 'm1', Pricing(1000, 1000, 1000))
    b = ledger.for_profile('same', 'p', 'm2')
    a.record(prompt=100, cached=0, completion=0)
    assert '~0.1000 USD' in ledger.session_line()
    b.record(prompt=100, cached=0, completion=0)
    assert len(ledger.profiles) == 2
    assert ledger.profiles[('same', 'p', 'm2')]['price_status'] == 'unknown'
    assert 'cost unknown' in ledger.session_line()


def test_profile_only_cost_budget_requires_explicit_startup_and_rejects_raw_requests():
    env = {'ODESSY_CLOUD_BUDGET_COST': '.2'}
    with pytest.raises(CloudUsageConfigError, match='needs prices'):
        UsageLedger.from_env(env)
    ledger = UsageLedger.from_env(env, allow_profile_prices=True)
    priced = ledger.for_profile('priced', 'p', 'm', Pricing(1000, 1000, 1000))
    priced.check()
    with pytest.raises(CloudUsageConfigError, match='price'):
        ledger.check()
    priced.record(prompt=200, cached=0, completion=0)
    with pytest.raises(CloudBudgetExceeded):
        priced.check()


def test_process_ledger_initializes_profile_only_budget_once(monkeypatch):
    monkeypatch.setenv('ODESSY_CLOUD_BUDGET_COST', '2')
    ledger = process_ledger(allow_profile_prices=True)
    assert ledger is process_ledger()
    assert ledger.budget_cost == 2 and ledger.pricing is None
    with pytest.raises(CloudUsageConfigError, match='price'):
        ledger.for_profile('unpriced', 'p', 'm').check()
