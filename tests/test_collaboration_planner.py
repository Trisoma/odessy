"""Layer 2 planner: intent -> untrusted role plan -> agent.run approval binding."""
import asyncio

import pytest

from odessy.collaboration import (
    infer_flow, plan_from_intent, to_run_arguments, validate_plan,
    WorkPlan, PlanStep,
)
from odessy.collaboration.config import (
    CollaborationConfig, CollaborationSettings, ParticipantProfile,
)


def _config(roles=None, allowed=('advice', 'patch')):
    return CollaborationConfig(
        settings=CollaborationSettings(enabled=True, roles=roles or {}),
        participants={
            'claude': ParticipantProfile('claude', 'claude_code',
                roles=('requirements', 'review'), allowed_modes=allowed),
            'codex': ParticipantProfile('codex', 'codex',
                roles=('implementation',), allowed_modes=allowed),
        },
    )


@pytest.mark.parametrize('text,flow', [
    ('让 claude 写设计文档，然后交给 codex 实现', 'document_to_code'),
    ('claude writes the spec, then codex implements it', 'document_to_code'),
    ('让 codex 写代码，再让 claude 审查', 'code_to_review'),
    ('写完代码后 review 一下', 'code_to_review'),
    ('', ''),
    ('just chatting about the weather', ''),
])
def test_infer_flow(text, flow):
    assert infer_flow(text) == flow


def test_plan_binds_mentioned_names_to_correct_roles():
    config = _config()
    plan = plan_from_intent(config, '让 claude 写设计文档，然后交给 codex 实现', '/tmp/w')
    assert plan is not None
    assert plan.flow == 'document_to_code'
    assert [(s.role, s.participant, s.mode) for s in plan.steps] == [
        ('requirements', 'claude', 'advice'),
        ('implementation', 'codex', 'patch'),
    ]
    assert validate_plan(plan, config) == (True, '')


def test_plan_falls_back_to_role_bindings_when_no_names():
    config = _config({'requirements': ('claude',), 'implementation': ('codex',)})
    plan = plan_from_intent(config, 'write a design document then implement it')
    assert plan is not None
    assert [s.participant for s in plan.steps] == ['claude', 'codex']


def test_plan_returns_none_for_unresolvable_role():
    config = CollaborationConfig(
        settings=CollaborationSettings(enabled=True),
        participants={'claude': ParticipantProfile('claude', 'claude_code')},
    )
    assert plan_from_intent(config, 'write a document then implement it') is None


def test_plan_does_not_grant_authority():
    """A plan matching roles says nothing about permissions."""
    config = _config()
    plan = plan_from_intent(config, 'let claude write the spec then codex implement')
    assert plan is not None
    # Matching returns names only; the participant's own modes are unchanged.
    assert 'patch' in config.participants['codex'].allowed_modes
    assert plan.steps[0].mode == 'advice'  # bound by flow, not granted by planner


def test_validate_plan_rejects_wrong_mode_and_unconfigured_participant():
    config = _config()
    bad = WorkPlan('document_to_code', (
        PlanStep('requirements', 'x', 'claude', 'patch'),  # wrong mode for step 1
        PlanStep('implementation', 'y', 'codex', 'patch'),
    ))
    ok, reason = validate_plan(bad, config)
    assert not ok and 'mode' in reason

    missing = WorkPlan('document_to_code', (
        PlanStep('requirements', 'x', 'ghost', 'advice'),
        PlanStep('implementation', 'y', 'codex', 'patch'),
    ))
    ok, reason = validate_plan(missing, config)
    assert not ok and 'not configured' in reason


def test_validate_plan_rejects_mode_outside_allowed_modes():
    config = _config(allowed=('advice',))  # nobody may patch
    plan = plan_from_intent(config, 'let claude write the spec then codex implement')
    assert plan is not None
    ok, reason = validate_plan(plan, config)
    assert not ok and 'does not allow patch' in reason


def test_to_run_arguments_maps_to_agent_run_schema():
    config = _config()
    plan = plan_from_intent(config, 'let claude write the design doc then codex implement', '/tmp/w')
    args = to_run_arguments(plan, config)
    assert set(args) == {'flow', 'from_participant', 'from_task',
                         'to_participant', 'to_task', 'workspace'}
    assert args['flow'] == 'document_to_code'
    assert args['from_participant'] == 'claude'
    assert args['to_participant'] == 'codex'
    assert args['workspace'] == '/tmp/w'


def test_to_run_arguments_rejects_invalid_plan():
    config = _config()
    bad = WorkPlan('nonsense', (
        PlanStep('r', 'x', 'claude', 'advice'),
        PlanStep('i', 'y', 'codex', 'patch'),
    ))
    with pytest.raises(ValueError):
        to_run_arguments(bad, config)


def _app(tmp_path, config):
    from odessy.application.api import create_app
    from odessy.application.session import SessionController

    class Agent:
        def __init__(self, model, **kwargs):
            pass
        async def run_turn(self, text, **kwargs):
            from odessy.runtime.types import AgentTurnResult
            return AgentTurnResult('answer', [])

    factory = lambda: SessionController(model_factory=lambda name: name,
        agent_factory=Agent, profiles={'local': {}})
    return create_app(factory, config=config, config_path=tmp_path / 'p.toml', token='fixture')


def test_submit_plan_via_session_uses_existing_approval(tmp_path):
    """submit_plan reuses agent.run approval; declining returns 'declined'."""
    httpx = pytest.importorskip('httpx')
    app = _app(tmp_path, _config())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer fixture'}) as client:
            created = await client.post('/v1/sessions', json={})
            assert created.status_code in (200, 201)
            sid = created.json()['id']
            controller = app.state.sessions[sid]
            controller.collaboration = type('C', (), {'config': _config()})()
            seen = {}

            async def fake_confirm(decision, request):
                seen['request'] = request
                return False  # decline

            controller.runs = object()  # make runs available
            controller.confirm = fake_confirm
            response = await client.post(f'/v1/sessions/{sid}/plan',
                                         json={'intent': 'let claude write the design doc then codex implement'})
            assert response.status_code == 200
            body = response.json()
            assert body['status'] == 'declined'
            assert body['plan']['flow'] == 'document_to_code'
            assert seen['request'].name == 'agent.run'
            assert seen['request'].arguments['from_participant'] == 'claude'
            assert seen['request'].arguments['to_participant'] == 'codex'
    asyncio.run(run())


def test_submit_plan_api_rejects_bad_input(tmp_path):
    httpx = pytest.importorskip('httpx')
    app = _app(tmp_path, _config())
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                                     headers={'Authorization': 'Bearer fixture'}) as client:
            sid = (await client.post('/v1/sessions', json={})).json()['id']
            assert (await client.post(f'/v1/sessions/{sid}/plan', json={'intent': 123})).status_code == 400
            assert (await client.post(f'/v1/sessions/{sid}/plan', json={'bogus': 'x'})).status_code == 400
            assert (await client.post(f'/v1/sessions/no-such/plan', json={'intent': 'x'})).status_code == 404
    asyncio.run(run())
