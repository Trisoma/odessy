from concurrent.futures import ThreadPoolExecutor
import asyncio
from datetime import datetime, timedelta, timezone
import pytest

from odessy.models.usage import Pricing, UsageLedger, set_usage_history
from odessy.models.usage_history import UsageHistory


@pytest.mark.parametrize('days', [7, 30, 90])
def test_date_range_uses_utc_calendar_days(tmp_path, days):
    history = UsageHistory(tmp_path / 'usage.sqlite3')
    today = datetime.now(timezone.utc).date()
    def write(day):
        history.record(provider='p', model='m', participant=None, prompt=1,
                       cached=0, completion=1, cost=None, currency=None,
                       timestamp=day.isoformat() + 'T00:00:00Z')
    write(today - timedelta(days=days))
    write(today - timedelta(days=days - 1))
    write(today)
    write(today + timedelta(days=1))
    result = history.query(days=days)
    assert result['total'] == 2


def test_history_persists_filters_and_preserves_unknown_values(tmp_path):
    history = UsageHistory(tmp_path / 'usage.sqlite3')
    history.record(provider='deepseek', model='model-a', participant='current',
                   prompt=100, cached=20, completion=30, cost=0.01, currency='USD',
                   timestamp='2026-11-05T12:00:00Z')
    history.record(provider='openai', model='model-b', participant='other',
                   prompt=None, cached=None, completion=10, cost=None, currency=None,
                   timestamp='2026-11-04T12:00:00Z')
    report = UsageHistory(tmp_path / 'usage.sqlite3').query(days=0, provider='deepseek')
    assert report['summary']['requests'] == 1
    assert report['summary']['prompt'] == 100
    assert report['summary']['cost'] == 0.01
    assert report['daily'][0]['day'] == '2026-11-05'
    all_records = history.query(days=0)
    assert all_records['summary']['cost'] is None
    assert all_records['requests'][1]['prompt'] is None
    assert {item['provider'] for item in all_records['providers']} == {'deepseek', 'openai'}
    assert history.query(days=0, model='model-b')['summary']['prompt'] is None


def test_ledger_records_profile_calls_and_history_failure_does_not_break_usage(tmp_path):
    history = UsageHistory(tmp_path / 'usage.sqlite3')
    set_usage_history(history)
    try:
        ledger = UsageLedger()
        profile = ledger.for_profile('current', 'deepseek', 'model-a', Pricing(1, 0.5, 2))
        profile.record(prompt=100, cached=20, completion=30)
        assert history.query(days=0)['requests'][0]['model'] == 'model-a'
        assert history.query(days=0)['requests'][0]['cost'] == 0.00015
        class BrokenHistory:
            def record(self, **kwargs):
                raise OSError('private path')
        set_usage_history(BrokenHistory())
        profile.record(prompt=1, cached=0, completion=1)
        assert ledger.total.calls == 2
    finally:
        set_usage_history(None)


def test_concurrent_writes_and_pagination(tmp_path):
    history = UsageHistory(tmp_path / 'usage.sqlite3')
    def write(i):
        history.record(provider='p', model='m', participant=None, prompt=i,
                       cached=None, completion=1, cost=None, currency=None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(24)))
    page = history.query(days=0, limit=10, offset=10)
    assert page['summary']['requests'] == 24
    assert len(page['requests']) == 10
    assert page['total'] == 24


def test_history_endpoint_requires_auth_and_validates_queries(tmp_path):
    httpx = pytest.importorskip('httpx')
    pytest.importorskip('fastapi')
    from odessy.application.api import create_app
    history = UsageHistory(tmp_path / 'usage.sqlite3')
    history.record(provider='p', model='m', participant=None, prompt=3, cached=None,
                   completion=2, cost=None, currency=None)
    async def check():
        app = create_app(lambda: None, token='secret', usage_history=history)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/v1/usage/history')).status_code == 401
            auth = {'Authorization': 'Bearer secret'}
            assert (await client.get('/v1/usage/history?days=999', headers=auth)).status_code == 400
            assert (await client.get('/v1/usage/history?limit=101', headers=auth)).status_code == 400
            response = await client.get('/v1/usage/history?days=0', headers=auth)
            assert response.status_code == 200
            assert response.json()['summary']['requests'] == 1
    asyncio.run(check())


def test_provider_reply_records_only_metadata(tmp_path):
    from odessy.models.cloud_model import CloudModel
    history = UsageHistory(tmp_path / 'usage.sqlite3')
    set_usage_history(history)
    try:
        ledger = UsageLedger()
        profile = ledger.for_profile('current', 'deepseek', 'model-a')
        model = CloudModel(provider='deepseek', model='model-a', api_key='secret-key', usage=profile)
        model._post_chat = lambda payload: {'choices': [{'message': {'content': 'private response'}}],
            'usage': {'prompt_tokens': 34, 'prompt_cache_hit_tokens': 8, 'completion_tokens': 7}}
        model.propose('private prompt')
        row = history.query(days=0)['requests'][0]
        assert (row['provider'], row['model'], row['prompt'], row['cached'], row['completion']) == (
            'deepseek', 'model-a', 34, 8, 7)
        assert row['cost'] is None
        contents = history.path.read_bytes()
        assert b'private prompt' not in contents
        assert b'private response' not in contents
        assert b'secret-key' not in contents
    finally:
        set_usage_history(None)


def test_mixed_currency_and_bad_counts_are_not_misrepresented(tmp_path):
    history = UsageHistory(tmp_path / 'usage.sqlite3')
    set_usage_history(history)
    try:
        ledger = UsageLedger()
        ledger.for_profile('a', 'one', 'm', Pricing(1, 1, 1, 'USD')).record(prompt=10, cached=20, completion=2)
        row = history.query(days=0)['requests'][0]
        assert row['cached'] is None and row['cost'] is None
        ledger.record(prompt=-1, cached=None, completion=None)
        assert history.query(days=0)['total'] == 1
    finally:
        set_usage_history(None)
    history.record(provider='p', model='m', participant=None, prompt=3, cached=0,
                   completion=2, cost=1, currency='EUR')
    history.record(provider='p', model='m', participant=None, prompt=3, cached=0,
                   completion=2, cost=1, currency='USD')
    assert history.query(days=0)['summary']['cost'] is None


def test_database_recreated_after_external_removal(tmp_path):
    history = UsageHistory(tmp_path / 'usage.sqlite3')
    assert history.query(days=0)['total'] == 0
    history.path.unlink()
    history.record(provider='p', model='m', participant=None, prompt=1, cached=0,
                   completion=2, cost=None, currency=None)
    assert history.query(days=0)['total'] == 1


def test_recording_failure_is_flagged_without_exposing_exception(tmp_path, caplog):
    history = UsageHistory(tmp_path / 'usage.sqlite3')
    def fail(**kwargs):
        raise OSError('sensitive-path')
    history.record = fail
    set_usage_history(history)
    try:
        UsageLedger().record(prompt=1, cached=0, completion=1)
        assert history.query(days=0)['recording_error'] is True
        assert 'sensitive-path' not in caplog.text
    finally:
        set_usage_history(None)
