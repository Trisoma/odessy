import asyncio
import httpx
from odessy.application.api import create_app
from odessy.collaboration.config import CollaborationConfig, ParticipantProfile, load_config


def test_model_limits_are_versioned_and_preserve_other_fields(tmp_path):
    async def run():
        path = tmp_path / 'profiles.toml'
        config = CollaborationConfig(participants={'assistant': ParticipantProfile(
            'assistant', 'cloud', model='existing', credential_env='PRIVATE_KEY')})
        app = create_app(lambda: None, config=config, config_path=path, token='fixture')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                    headers={'Authorization': 'Bearer fixture'}) as client:
            route = '/v1/settings/model-profiles/assistant/limits'
            before = (await client.get(route)).json()
            assert before['timeout'] == 120 and before['max_tokens'] == 4096
            update = {**before, 'timeout': 45, 'max_tokens': 2000}
            assert (await client.put(route, json={**update, 'timeout': True})).status_code == 400
            assert (await client.put(route, json={**update, 'max_tokens': 1000001})).status_code == 400
            assert (await client.put(route, json=update)).status_code == 200
            saved = load_config(path).participants['assistant']
            assert (saved.timeout, saved.max_tokens, saved.model, saved.credential_env) == (45, 2000, 'existing', 'PRIVATE_KEY')
            assert (await client.put(route, json=update)).status_code == 409
            assert (await client.get('/v1/settings/model-profiles/missing/limits')).status_code == 404
    asyncio.run(run())


def test_model_profiles_guarded_edit_preserves_credentials_and_other_fields(tmp_path):
    async def run():
        path = tmp_path / 'profiles.toml'
        profile = ParticipantProfile('assistant', 'cloud', model='old', provider='openai',
            endpoint='https://api.example.test', credential_env='PRIVATE_KEY', timeout=63)
        config = CollaborationConfig(participants={'assistant': profile,
            'coder': ParticipantProfile('coder', 'codex')})
        app = create_app(lambda: None, config=config, config_path=path, token='fixture')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as unauthenticated:
            assert (await unauthenticated.get('/v1/settings/model-profiles')).status_code == 401
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                    headers={'Authorization': 'Bearer fixture'}) as client:
            route = '/v1/settings/model-profiles'
            snapshot = (await client.get(route)).json()
            assert [p['name'] for p in snapshot['profiles']] == ['assistant']
            assert 'PRIVATE_KEY' not in str(snapshot)
            request = {'instance_id': snapshot['instance_id'], 'revision': snapshot['revision'],
                       'model': 'new-model', 'endpoint': 'https://other.example.test/v1'}
            assert (await client.put(route + '/assistant', json={**request, 'endpoint': 'https://u:p@example.test'})).status_code == 400
            assert not path.exists()
            response = await client.put(route + '/assistant', json=request)
            assert response.status_code == 200
            assert response.json()['revision'] == snapshot['revision'] + 1
            saved = load_config(path).participants['assistant']
            assert (saved.model, saved.endpoint, saved.credential_env, saved.timeout) == (
                'new-model', 'https://other.example.test/v1', 'PRIVATE_KEY', 63)
            assert load_config(path).participants['coder'].adapter == 'codex'
            assert (await client.put(route + '/assistant', json=request)).status_code == 409
            fresh = (await client.get(route)).json()
            assert (await client.put(route + '/coder', json={**request, 'revision': fresh['revision']})).status_code == 404
            assert (await client.get('/v1/settings/consultation-limit')).json()['revision'] == fresh['revision']
    asyncio.run(run())


def test_provider_switch_resets_prices_and_refuses_custom_credentials(tmp_path):
    from odessy.models.usage import Pricing
    async def run():
        path = tmp_path / 'profiles.toml'
        config = CollaborationConfig(participants={
            'plain': ParticipantProfile('plain', 'cloud', provider='openai', model='old-model',
                endpoint='https://old.example.test', pricing=Pricing(1, 0.5, 2), timeout=63),
            'protected': ParticipantProfile('protected', 'cloud', provider='openai', credential_env='PRIVATE_KEY'),
            'local': ParticipantProfile('local', 'ollama')})
        app = create_app(lambda: None, config=config, config_path=path, token='fixture')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                    headers={'Authorization': 'Bearer fixture'}) as client:
            route = '/v1/settings/model-profiles'
            snapshot = (await client.get(route)).json()
            version = {key: snapshot[key] for key in ('instance_id', 'revision')}
            update = {**version, 'provider': 'claude', 'model': '', 'endpoint': ''}
            assert (await client.put(route + '/protected/provider', json=update)).status_code == 409
            assert (await client.put(route + '/local/provider', json=update)).status_code == 404
            assert (await client.put(route + '/plain/provider', json={**update, 'provider': 'unknown'})).status_code == 400
            assert (await client.put(route + '/plain/provider', json={**update, 'provider': 'openai'})).status_code == 400
            assert (await client.put(route + '/plain/provider', json={**update, 'provider': 'custom'})).status_code == 400
            assert (await client.put(route + '/plain/provider', json={**update, 'endpoint': 'https://secret@example.test'})).status_code == 400
            assert not path.exists()
            response = await client.put(route + '/plain/provider', json=update)
            assert response.status_code == 200
            saved = load_config(path).participants['plain']
            assert (saved.provider, saved.model, saved.endpoint, saved.pricing, saved.timeout) == ('claude', '', '', None, 63)
            assert load_config(path).participants['protected'].credential_env == 'PRIVATE_KEY'
            assert (await client.put(route + '/plain/provider', json=update)).status_code == 409
    asyncio.run(run())


def test_pricing_validates_currency_conflicts_and_preserves_profiles(tmp_path, monkeypatch):
    from odessy.models.usage import Pricing
    async def run():
        path = tmp_path / 'profiles.toml'
        config = CollaborationConfig(participants={
            'plain': ParticipantProfile('plain', 'cloud', provider='openai', credential_env='PRIVATE_KEY'),
            'other': ParticipantProfile('other', 'cloud', provider='claude', pricing=Pricing(1, 0.5, 2, 'USD')),
            'local': ParticipantProfile('local', 'ollama')})
        app = create_app(lambda: None, config=config, config_path=path, token='fixture')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                    headers={'Authorization': 'Bearer fixture'}) as client:
            route = '/v1/settings/model-profiles/plain/pricing'
            snapshot = (await client.get(route)).json()
            assert snapshot['pricing'] is None
            version = {key: snapshot[key] for key in ('instance_id', 'revision')}
            rate = {'input': 2.5, 'cached_input': 0.5, 'output': 5, 'currency': 'USD'}
            assert (await client.put(route, json={**version, 'pricing': {**rate, 'input': True}})).status_code == 400
            assert (await client.put(route, json={**version, 'pricing': {**rate, 'input': 10**309}})).status_code == 400
            assert (await client.put(route, json={**version, 'pricing': {**rate, 'currency': 'EUR'}})).status_code == 409
            assert (await client.put('/v1/settings/model-profiles/local/pricing', json={**version, 'pricing': rate})).status_code == 404
            assert not path.exists()
            response = await client.put(route, json={**version, 'pricing': rate})
            assert response.status_code == 200
            saved = load_config(path).participants['plain']
            assert saved.pricing == Pricing(2.5, 0.5, 5, 'USD')
            assert saved.credential_env == 'PRIVATE_KEY'
            assert (await client.put(route, json={**version, 'pricing': None})).status_code == 409
            current = (await client.get(route)).json()
            assert current['pricing']['input'] == 2.5
            assert (await client.put(route, json={**{k: current[k] for k in ('instance_id', 'revision')}, 'pricing': None})).status_code == 200
            assert load_config(path).participants['plain'].pricing is None
    for variable in ('ODESSY_CLOUD_PRICE_INPUT', 'ODESSY_CLOUD_PRICE_CACHED_INPUT',
                     'ODESSY_CLOUD_PRICE_OUTPUT', 'ODESSY_CLOUD_PRICE_CURRENCY'):
        monkeypatch.delenv(variable, raising=False)
    asyncio.run(run())


def test_credential_reference_is_metadata_only_and_version_guarded(tmp_path, monkeypatch):
    async def run():
        path = tmp_path / 'profiles.toml'
        profile = ParticipantProfile('plain', 'cloud', provider='openai', credential_env='PRIVATE_KEY', model='model')
        app = create_app(lambda: None, config=CollaborationConfig(participants={'plain': profile}),
                         config_path=path, token='fixture')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                    headers={'Authorization': 'Bearer fixture'}) as client:
            route = '/v1/settings/model-profiles/plain/credential-reference'
            response = await client.get(route)
            assert response.status_code == 200
            assert 'super-secret-fixture' not in response.text
            snapshot = response.json()
            assert snapshot['credential_env'] == 'PRIVATE_KEY'
            assert snapshot['default_credential_env'] == 'OPENAI_API_KEY'
            version = {key: snapshot[key] for key in ('instance_id', 'revision')}
            for invalid in ['sk-secret-value', 'ODESSY_API_TOKEN', 'ODESSY_INTERNAL_ACCESS_TOKEN', 'lower_api_key']:
                assert (await client.put(route, json={**version, 'credential_env': invalid})).status_code == 400
            assert not path.exists()
            saved = await client.put(route, json={**version, 'credential_env': 'CUSTOM_API_KEY'})
            assert saved.status_code == 200
            assert 'super-secret-fixture' not in saved.text
            assert load_config(path).participants['plain'].credential_env == 'CUSTOM_API_KEY'
            assert load_config(path).participants['plain'].model == 'model'
            assert (await client.put(route, json={**version, 'credential_env': ''})).status_code == 409
            current = (await client.get(route)).json()
            assert (await client.put(route, json={**{k: current[k] for k in ('instance_id', 'revision')},
                                                   'credential_env': ''})).status_code == 200
            assert load_config(path).participants['plain'].credential_env == ''
            assert 'super-secret-fixture' not in path.read_text(encoding='utf-8')
            latest = (await client.get(route)).json()
            switch = await client.put('/v1/settings/model-profiles/plain/provider', json={
                **{k: latest[k] for k in ('instance_id', 'revision')},
                'provider': 'claude', 'model': '', 'endpoint': ''})
            assert switch.status_code == 200
            assert load_config(path).participants['plain'].effective_credential_env == 'ANTHROPIC_API_KEY'
    monkeypatch.setenv('CUSTOM_API_KEY', 'super-secret-fixture')
    asyncio.run(run())


def test_create_delete_validates_identity_and_references(tmp_path):
    from odessy.collaboration.config import CollaborationSettings
    async def run():
        path = tmp_path / 'profiles.toml'
        config = CollaborationConfig(settings=CollaborationSettings(default_participant='existing'),
            participants={'existing': ParticipantProfile('existing', 'ollama'),
                          'coder': ParticipantProfile('coder', 'codex')})
        app = create_app(lambda: None, config=config, config_path=path, token='fixture')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                    headers={'Authorization': 'Bearer fixture'}) as client:
            route = '/v1/settings/model-profiles'
            initial = (await client.get(route)).json()
            version = {key: initial[key] for key in ('instance_id', 'revision')}
            providers = (await client.get('/v1/settings/model-profile-providers')).json()['providers']
            assert any(item['name'] == 'openai' for item in providers)
            new = {**version, 'name': 'work', 'adapter': 'cloud', 'provider': 'openai',
                   'model': 'example', 'endpoint': 'https://example.test/v1'}
            assert (await client.post(route, json={**new, 'name': 'local'})).status_code == 400
            assert (await client.post(route, json={**new, 'name': 'bad/name'})).status_code == 400
            assert (await client.post(route, json={**new, 'name': 'coder'})).status_code == 409
            assert (await client.post(route, json={**new, 'provider': 'unknown'})).status_code == 400
            assert (await client.post(route, json={**new, 'provider': 'custom', 'endpoint': ''})).status_code == 400
            assert (await client.post(route, json={**new, 'endpoint': 'https://u:p@example.test'})).status_code == 400
            created = await client.post(route, json=new)
            assert created.status_code == 201
            assert load_config(path).participants['work'].credential_env == ''
            assert (await client.request('DELETE', route + '/work', json=version)).status_code == 409
            current = {key: created.json()[key] for key in ('instance_id', 'revision')}
            assert (await client.request('DELETE', route + '/existing', json=current)).status_code == 409
            assert (await client.request('DELETE', route + '/coder', json=current)).status_code == 404
            deleted = await client.request('DELETE', route + '/work', json=current)
            assert deleted.status_code == 200
            assert 'work' not in load_config(path).participants
            assert 'existing' in load_config(path).participants
    asyncio.run(run())
