"""Access-mode semantics: real reviewer, exact binding, revocation and isolation."""
from __future__ import annotations

import pytest

from odessy.application.access_modes import (
    AccessModeError,
    AccessModeService,
    REVIEWER,
)
from odessy.application.agent_basics import (
    BasicsDependencies,
    ReviewInput,
    ReviewResult,
)


class Controller:
    def __init__(self, engine: str = 'legacy') -> None:
        self.engine = engine


def service_for(*, sessions=None, scope=None, engine='legacy', clock=None,
                ttl=900.0) -> tuple[AccessModeService, dict]:
    live = sessions if sessions is not None else {'s1': Controller(engine),
                                                  's2': Controller(engine)}
    dependencies = BasicsDependencies('instance-1', lambda sid: live[sid])

    def resolve(sid, controller):
        if scope is not None:
            return scope(sid, controller)
        return {'workspace_id': 'ws-1', 'target_id': 'local-laptop'}

    return AccessModeService(dependencies, resolve_scope=resolve, clock=clock,
                             grant_ttl=ttl), live


def review(sid='s1', *, decision='confirm', operation='filesystem.write',
           rule='workspace_write', risk='medium', scopes=('authorization',),
           content_bound=True, scope_authorized=True, binding=True,
           revision=None, fingerprint='fp-1', service='instance-1') -> ReviewInput:
    return ReviewInput(
        session_id=sid,
        service_instance_id=service,
        mode_revision=0 if revision is None else revision,
        operation=operation,
        fingerprint=fingerprint,
        decision=decision,
        rule=rule,
        risk=risk,
        scopes=tuple(scopes),
        scope_authorized=scope_authorized,
        current_binding=binding,
        content_bound_review=content_bound,
    )


def set_mode(service: AccessModeService, mode: str, *, sid='s1', revision=0) -> dict:
    return service.set_mode(sid, mode=mode, expected_revision=revision,
                            service_instance_id='instance-1')


def test_defaults_are_ask_revision_zero_and_reading_grants_nothing():
    service, _ = service_for()
    first = service.snapshot('s1')
    assert first == {
        'version': 1,
        'session_id': 's1',
        'service_instance_id': 'instance-1',
        'mode': 'ask',
        'revision': 0,
        'supported_modes': ['ask', 'auto', 'full'],
        'reviewer': REVIEWER,
        'scope': {'workspace_id': 'ws-1', 'target_id': 'local-laptop'},
        'effective': True,
    }
    for _ in range(3):
        assert service.snapshot('s1') == first
    assert service.records() == ()


def test_set_mode_bumps_revision_and_rejects_stale_expected_revision():
    service, _ = service_for()
    changed = set_mode(service, 'auto')
    assert changed['mode'] == 'auto' and changed['revision'] == 1
    with pytest.raises(AccessModeError) as stale:
        set_mode(service, 'full', revision=0)
    assert stale.value.code == 409
    assert service.snapshot('s1')['revision'] == 1
    with pytest.raises(AccessModeError) as future:
        set_mode(service, 'full', revision=99)
    assert future.value.code == 409


@pytest.mark.parametrize('mode', ['ask', 'auto', 'full'])
def test_policy_allow_stays_allowed_and_deny_stays_denied(mode):
    service, _ = service_for()
    if mode != 'ask':
        set_mode(service, mode)
    assert service.review(review(decision='allow')).outcome == 'approve'
    denied = service.review(review(decision='deny'))
    assert denied.outcome == 'deny'
    # A denial is not a grant, so the dispatch barrier refuses it.
    assert service.validate(denied, review(decision='deny')) is False


def test_ask_mode_never_auto_approves_a_confirming_action():
    service, _ = service_for()
    result = service.review(review())
    assert result.outcome == 'ask'
    assert 'ask mode' in result.reason
    assert service.validate(result, review()) is False


def test_auto_approves_exactly_the_bounded_workspace_write():
    service, _ = service_for()
    set_mode(service, 'auto')
    request = review(revision=1)
    result = service.review(request)
    assert result.outcome == 'approve'
    assert result.fingerprint == 'fp-1' and result.mode_revision == 1
    assert 'policy-v1' in result.reason
    assert service.validate(result, request) is True
    record = service.records()[-1]
    assert (record.operation, record.rule, record.risk, record.scopes) == (
        'filesystem.write', 'workspace_write', 'medium', ('authorization',))
    assert record.outcome == 'approve' and record.revision == 1


@pytest.mark.parametrize('overrides,expected', [
    ({'operation': 'terminal.run'}, 'does not cover'),
    ({'operation': 'agent.delegate'}, 'does not cover'),
    ({'rule': 'hook:pre-write'}, 'only covers the workspace_write rule'),
    ({'rule': None}, 'not no rule'),
    ({'risk': 'high'}, 'only covers medium risk'),
    ({'risk': 'low'}, 'only covers medium risk'),
    ({'scopes': ('authorization', 'escalation')}, 'authorization-only'),
    ({'scopes': ()}, 'authorization-only'),
    ({'content_bound': False}, 'content-bound diff review'),
    ({'binding': False}, 'changed since screening'),
    ({'scope_authorized': False}, 'outside its authorized scope'),
])
def test_auto_escalates_everything_outside_its_bounded_class(overrides, expected):
    service, _ = service_for()
    set_mode(service, 'auto')
    request = review(revision=1, **overrides)
    result = service.review(request)
    assert result.outcome == 'ask'
    assert expected in result.reason
    assert service.validate(result, request) is False


def test_full_suppresses_only_authorization_confirmations():
    service, _ = service_for()
    set_mode(service, 'full')
    ordinary = review(revision=1, operation='terminal.run', rule='process_exec',
                      risk='high', content_bound=False)
    result = service.review(ordinary)
    assert result.outcome == 'approve'
    assert service.validate(result, ordinary) is True

    escalation = service.review(review(revision=1, scopes=('authorization', 'escalation')))
    assert escalation.outcome == 'ask'
    assert 'escalation' in escalation.reason

    hook = service.review(review(revision=1, rule='hook:pre-write'))
    assert hook.outcome == 'ask'
    assert 'hook confirmation' in hook.reason

    other = service.review(review(revision=1, scopes=('authorization', 'network')))
    assert other.outcome == 'ask'
    assert 'network' in other.reason


def test_grant_is_single_use_and_bound_to_the_exact_fingerprint():
    service, _ = service_for()
    set_mode(service, 'auto')
    request = review(revision=1)
    result = service.review(request)
    assert service.validate(result, request) is True
    # Replaying the same decision cannot authorize a second dispatch.
    assert service.validate(result, request) is False

    second = review(revision=1, fingerprint='fp-2')
    approved = service.review(second)
    assert service.validate(approved, review(revision=1, fingerprint='fp-3')) is False


def test_grant_prepared_for_an_older_revision_cannot_dispatch_after_a_mode_change():
    service, _ = service_for()
    set_mode(service, 'auto')
    request = review(revision=1)
    result = service.review(request)
    assert result.outcome == 'approve'
    set_mode(service, 'full', revision=1)
    assert service.validate(result, request) is False
    rebuilt = review(revision=2)
    assert service.validate(service.review(rebuilt), rebuilt) is True


def test_cross_session_grants_do_not_leak():
    service, _ = service_for()
    set_mode(service, 'auto', sid='s1')
    first = review(sid='s1', revision=1)
    grant = service.review(first)
    assert service.validate(grant, first) is True
    other = review(sid='s2', revision=1, fingerprint='fp-1')
    assert service.validate(grant, other) is False
    service.set_mode('s2', mode='auto', expected_revision=0,
                     service_instance_id='instance-1')
    second = review(sid='s2', revision=1, fingerprint='fp-1')
    assert service.validate(service.review(second), second) is True


def test_forged_or_historical_results_never_validate():
    service, _ = service_for()
    set_mode(service, 'auto')
    request = review(revision=1)
    forged = ReviewResult(outcome='approve', reason='looks fine', fingerprint='fp-1',
                          mode_revision=1)
    assert service.validate(forged, request) is False


def test_close_invalidates_mode_revision_and_grants():
    service, _ = service_for()
    set_mode(service, 'auto')
    request = review(revision=1)
    result = service.review(request)
    service.close('s1')
    assert service.validate(result, request) is False
    fresh = service.snapshot('s1')
    assert fresh['mode'] == 'ask' and fresh['revision'] == 0
    # The old result belongs to the previous lifetime of the session.
    again = review(revision=1)
    assert service.review(again).outcome == 'ask'


def test_expired_grant_is_refused():
    now = [1000.0]
    service, _ = service_for(clock=lambda: now[0], ttl=5.0)
    set_mode(service, 'auto')
    request = review(revision=1)
    result = service.review(request)
    now[0] += 6.0
    assert service.validate(result, request) is False


def test_service_identity_mismatch_asks_instead_of_approving():
    service, _ = service_for()
    set_mode(service, 'auto')
    request = review(revision=1, service='another-instance')
    result = service.review(request)
    assert result.outcome == 'ask'
    assert 'service identity' in result.reason


def test_harness_reports_no_supported_mode_and_refuses_mutation():
    service, _ = service_for(engine='harness', sessions={'s1': Controller('harness')})
    snapshot = service.snapshot('s1')
    assert snapshot['supported_modes'] == []
    assert snapshot['effective'] is False
    with pytest.raises(AccessModeError) as unsupported:
        set_mode(service, 'auto')
    assert unsupported.value.code == 501
    assert service.review(review()).outcome == 'ask'


def test_missing_live_scope_is_not_effective_and_cannot_approve():
    service, _ = service_for(scope=lambda sid, controller: None)
    assert service.snapshot('s1')['effective'] is False
    assert service.snapshot('s1')['scope'] is None
    with pytest.raises(AccessModeError) as no_scope:
        set_mode(service, 'auto')
    assert no_scope.value.code == 409
    assert service.review(review()).outcome == 'ask'


def test_scope_drift_requires_a_new_mode_revision():
    current = {'workspace_id': 'ws-1', 'target_id': 'local-laptop'}
    service, _ = service_for(scope=lambda sid, controller: dict(current))
    set_mode(service, 'auto')
    request = review(revision=1)
    result = service.review(request)
    current['workspace_id'] = 'ws-2'
    assert service.snapshot('s1')['effective'] is False
    assert service.validate(result, request) is False
    assert service.review(review(revision=1)).outcome == 'ask'


def test_unknown_session_is_a_missing_resource():
    service, _ = service_for()
    with pytest.raises(AccessModeError) as missing:
        service.snapshot('nope')
    assert missing.value.code == 404
    assert service.review(review(sid='nope')).outcome == 'ask'


def test_invalid_arguments_are_rejected_before_state_changes():
    service, _ = service_for()
    for bad in ({'mode': 'yolo', 'expected_revision': 0, 'service_instance_id': 'instance-1'},
                {'mode': 'auto', 'expected_revision': True, 'service_instance_id': 'instance-1'},
                {'mode': 'auto', 'expected_revision': -1, 'service_instance_id': 'instance-1'},
                {'mode': 'auto', 'expected_revision': 0, 'service_instance_id': ''}):
        with pytest.raises(AccessModeError) as error:
            service.set_mode('s1', mode=bad['mode'],
                             expected_revision=bad['expected_revision'],
                             service_instance_id=bad['service_instance_id'])
        assert error.value.code == 400
    assert service.snapshot('s1')['revision'] == 0


def test_review_requires_a_trusted_input_object():
    service, _ = service_for()
    with pytest.raises(TypeError):
        service.review({'session_id': 's1'})
