"""Role profile layer: configuration, persistence and scheduling-only matching."""
import asyncio

import pytest

from odessy.collaboration.config import (
    CollaborationConfig, CollaborationSettings, ParticipantProfile, load_config, save_config,
)
from odessy.collaboration.roles import (
    bindings_for, declared_roles, resolve_role, tagged_for,
)


def _config(roles=None, participants=None):
    return CollaborationConfig(
        settings=CollaborationSettings(enabled=True, roles=roles or {}),
        participants=participants if participants is not None else {},
    )


def test_declared_roles_union_of_bindings_and_tags():
    config = _config(
        roles={'requirements': ('claude',)},
        participants={
            'claude': ParticipantProfile('claude', 'claude_code', roles=('requirements', 'review')),
            'codex': ParticipantProfile('codex', 'codex', roles=('implementation',)),
        },
    )
    assert declared_roles(config) == ('implementation', 'requirements', 'review')


def test_explicit_binding_wins_over_capability_tag():
    config = _config(
        roles={'review': ('codex',)},
        participants={
            'claude': ParticipantProfile('claude', 'claude_code', roles=('review',)),
            'codex': ParticipantProfile('codex', 'codex'),
        },
    )
    assert tagged_for(config, 'review') == ('claude',)
    assert bindings_for(config, 'review') == ('codex',)
    assert resolve_role(config, 'review') == ('codex',)


def test_tag_is_fallback_when_no_binding():
    config = _config(participants={
        'claude': ParticipantProfile('claude', 'claude_code', roles=('review',)),
        'codex': ParticipantProfile('codex', 'codex', roles=('review',)),
    })
    assert resolve_role(config, 'review') == ('claude', 'codex')


def test_stale_binding_never_selects_phantom_participant():
    config = _config(
        roles={'review': ('ghost', 'claude')},
        participants={'claude': ParticipantProfile('claude', 'claude_code', roles=('review',))},
    )
    assert bindings_for(config, 'review') == ('claude',)
    assert resolve_role(config, 'review') == ('claude',)


def test_resolve_deduplicates_and_ignores_unknown_role():
    config = _config(
        roles={'review': ('claude', 'codex')},
        participants={
            'claude': ParticipantProfile('claude', 'claude_code', roles=('review',)),
            'codex': ParticipantProfile('codex', 'codex', roles=('review',)),
        },
    )
    assert resolve_role(config, 'review') == ('claude', 'codex')
    assert resolve_role(config, '') == ()
    assert resolve_role(config, 'missing') == ()


def test_matching_does_not_grant_authority():
    """A role match must not imply consult/delegate/patch permission."""
    config = _config(
        roles={'write': ('writer',)},
        participants={'writer': ParticipantProfile('writer', 'codex',
            capabilities=('consult',), allowed_modes=('advice',))},
    )
    # Matching returns the candidate even though the participant cannot patch.
    assert resolve_role(config, 'write') == ('writer',)
    assert 'patch' not in config.participants['writer'].allowed_modes


def test_roles_roundtrip_through_toml(tmp_path):
    path = tmp_path / 'roles.toml'
    config = _config(
        roles={'requirements': ('claude',), 'review': ('claude', 'codex')},
        participants={
            'claude': ParticipantProfile('claude', 'claude_code', roles=('requirements', 'review')),
            'codex': ParticipantProfile('codex', 'codex', roles=('implementation',)),
        },
    )
    save_config(config, path)
    loaded = load_config(path)
    assert loaded == config
    assert loaded.settings.roles['review'] == ('claude', 'codex')
    assert loaded.participants['claude'].roles == ('requirements', 'review')


@pytest.mark.parametrize('text', [
    '[settings]\nroles = {"bad name" = ["claude"]}',
    '[settings]\nroles = {review = ["bad/name"]}',
    '[settings]\nroles = {review = ["claude", "claude"]}',
    '[participants.claude]\nadapter="codex"\nroles=["bad name"]',
])
def test_invalid_roles_rejected(tmp_path, text):
    path = tmp_path / 'bad.toml'
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError):
        load_config(path)


def test_older_config_without_roles_still_loads(tmp_path):
    path = tmp_path / 'legacy.toml'
    path.write_text('[settings]\nenabled = true\n[participants.reviewer]\nadapter = "ollama"\n', encoding='utf-8')
    config = load_config(path)
    assert config.settings.roles == {}
    assert config.participants['reviewer'].roles == ()


def test_roles_api_is_versioned_and_persists_bindings(tmp_path):
    httpx = pytest.importorskip('httpx')
    from odessy.application.api import create_app

    async def run():
        path = tmp_path / 'roles.toml'
        config = _config(participants={
            'claude': ParticipantProfile('claude', 'claude_code', roles=('requirements', 'review')),
            'codex': ParticipantProfile('codex', 'codex', roles=('implementation',)),
        })
        app = create_app(lambda: None, config=config, config_path=path, token='fixture')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as anon:
            assert (await anon.get('/v1/settings/roles')).status_code == 401
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer fixture'}) as client:
            before = (await client.get('/v1/settings/roles')).json()
            assert before['roles'] == {}
            assert before['participant_roles']['codex'] == ['implementation']
            version = {'instance_id': before['instance_id'], 'revision': before['revision']}
            bad = await client.put('/v1/settings/roles', json={**version, 'roles': {'bad name': ['claude']}})
            assert bad.status_code == 400
            assert not path.exists()
            response = await client.put('/v1/settings/roles', json={**version, 'roles': {
                'requirements': ['claude'], 'implementation': ['codex']}})
            assert response.status_code == 200
            body = response.json()
            assert body['revision'] == before['revision'] + 1
            assert body['resolved']['requirements'] == ['claude']
            assert set(body['known_roles']) == {'implementation', 'requirements', 'review'}
            saved = load_config(path)
            assert saved.settings.roles['requirements'] == ('claude',)
            assert saved.participants['claude'].roles == ('requirements', 'review')
            assert (await client.put('/v1/settings/roles', json={**version, 'roles': {}})).status_code == 409
    asyncio.run(run())
