import asyncio
import threading

import httpx

from odessy.application.api import create_app
from odessy.collaboration.config import CollaborationConfig, load_config


def test_collaboration_enabled_is_explicit_versioned_and_preserves_other_settings(tmp_path):
    async def run():
        path = tmp_path / 'config.toml'
        app = create_app(lambda: None, token='fixture', config=CollaborationConfig(), config_path=path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                    headers={'Authorization': 'Bearer fixture'}) as client:
            route = '/v1/settings/collaboration-enabled'
            original = (await client.get(route)).json()
            assert original['enabled'] is False
            assert (await client.put(route, json={**original, 'enabled': 1})).status_code == 400
            assert (await client.put(route, json={**original, 'enabled': True})).status_code == 200
            saved = load_config(path).settings
            assert saved.enabled is True and saved.automatic is False and saved.max_consultations == 1
            assert (await client.put(route, json=original)).status_code == 409
    asyncio.run(run())


def test_round_limit_is_validated_and_shares_settings_revision(tmp_path):
    async def run():
        path = tmp_path / 'config.toml'
        app = create_app(lambda: None, token='fixture', config=CollaborationConfig(), config_path=path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                    headers={'Authorization': 'Bearer fixture'}) as client:
            route = '/v1/settings/round-limit'
            before = (await client.get(route)).json()
            assert before['max_rounds'] == 1
            assert (await client.put(route, json={**before, 'max_rounds': True})).status_code == 400
            assert (await client.put(route, json={**before, 'max_rounds': 33})).status_code == 400
            assert (await client.put(route, json={**before, 'max_rounds': 0})).status_code == 200
            saved = load_config(path).settings
            assert saved.max_rounds == 0 and saved.max_consultations == 1 and saved.max_context_chars == 8000
            assert (await client.put(route, json=before)).status_code == 409
    asyncio.run(run())


def test_context_limit_is_versioned_and_preserves_other_settings(tmp_path):
    async def run():
        path = tmp_path / 'config.toml'
        app = create_app(lambda: None, token='fixture', config=CollaborationConfig(), config_path=path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                    headers={'Authorization': 'Bearer fixture'}) as client:
            route = '/v1/settings/context-limit'
            current = (await client.get(route)).json()
            assert current['max_context_chars'] == 8000
            assert (await client.put(route, json={**current, 'max_context_chars': True})).status_code == 400
            assert (await client.put(route, json={**current, 'max_context_chars': 100001})).status_code == 400
            assert (await client.put(route, json={**current, 'max_context_chars': 2000})).status_code == 200
            assert load_config(path).settings.max_context_chars == 2000
            assert load_config(path).settings.max_consultations == 1
            assert (await client.put(route, json=current)).status_code == 409
    asyncio.run(run())


def test_revision_conflicts_and_legacy_writes_preserve_other_settings(tmp_path):
    async def run():
        path = tmp_path / 'collaboration.toml'
        app = create_app(lambda: None, token='fixture', config=CollaborationConfig(), config_path=path)
        headers = {'Authorization': 'Bearer fixture'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            route = '/v1/settings/consultation-limit'
            assert (await client.get(route)).status_code == 401
            original = (await client.get(route, headers=headers)).json()
            first, second = await asyncio.gather(*[
                client.put(route, headers=headers, json={**original, 'max_consultations': value})
                for value in (3, 4)
            ])
            assert sorted([first.status_code, second.status_code]) == [200, 409]
            saved = (await client.get(route, headers=headers)).json()
            assert saved['revision'] == original['revision'] + 1
            assert load_config(path).settings.max_consultations == saved['max_consultations']
            assert (await client.put(route, headers=headers, json={**saved, 'instance_id': 'old'})).status_code == 409
            assert (await client.put(route, headers=headers, json={**saved, 'max_consultations': True})).status_code == 400
            legacy = (await client.get('/v1/settings', headers=headers)).json()
            legacy['settings']['max_rounds'] = 7
            assert (await client.put('/v1/settings', headers=headers, json=legacy)).status_code == 200
            assert (await client.put(route, headers=headers, json=saved)).status_code == 409
            current = (await client.get(route, headers=headers)).json()
            result = await client.put(route, headers=headers, json={**current, 'max_consultations': 8})
            assert result.status_code == 200
            assert load_config(path).settings.max_rounds == 7
            assert load_config(path).settings.max_consultations == 8
            other = create_app(lambda: None, token='fixture', config=CollaborationConfig(), config_path=path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=other), base_url='http://test') as restarted:
                assert (await restarted.put(route, headers=headers, json=current)).status_code == 409
    asyncio.run(run())


def test_cancelled_request_does_not_release_lock_during_file_write(tmp_path, monkeypatch):
    from odessy.collaboration import config as config_module
    entered, release = threading.Event(), threading.Event()
    original_save = config_module.save_config

    def slow_save(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_save(*args, **kwargs)

    monkeypatch.setattr(config_module, 'save_config', slow_save)

    async def run():
        app = create_app(lambda: None, token='fixture', config=CollaborationConfig(),
                         config_path=tmp_path / 'config.toml')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                    headers={'Authorization': 'Bearer fixture'}) as client:
            route = '/v1/settings/consultation-limit'
            initial = (await client.get(route)).json()
            first = asyncio.create_task(client.put(route, json={**initial, 'max_consultations': 3}))
            assert await asyncio.to_thread(entered.wait, 3)
            first.cancel()
            second = asyncio.create_task(client.put(route, json={**initial, 'max_consultations': 4}))
            await asyncio.sleep(.05)
            assert not second.done()
            release.set()
            try:
                await first
            except asyncio.CancelledError:
                pass
            assert (await second).status_code == 409
            assert (await client.get(route)).json()['max_consultations'] == 3
    try:
        asyncio.run(run())
    finally:
        release.set()
