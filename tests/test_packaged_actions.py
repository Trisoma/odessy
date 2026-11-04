import pytest
import json

def test_package_requires_verified_context_before_mutation(monkeypatch):
    from odessy.setup.native_bridge import manage
    from odessy.setup import package_context
    monkeypatch.setattr(package_context,'context',lambda **kwargs: (_ for _ in ()).throw(ValueError('damaged package')))
    with pytest.raises(ValueError,match='damaged package'):
        manage({'action':'package-review','root':'C:/test','payload':'C:/payload','selection':{'role':'combined'}})


def test_review_projection_omits_large_authoritative_inventories():
    from odessy.setup.native_bridge import _review_projection
    plan = {'plan_id': 'abc', 'root': '/app', 'components': ['desktop'],
            'selection': {'role': 'combined'},
            'previous_state': {'inventory': 'x' * (5 * 1024 * 1024)},
            'python_runtime': {'files': {'runtime': 'x' * (5 * 1024 * 1024)}},
            'payload_manifest': {'files': {'payload': 'x' * (5 * 1024 * 1024)}}}
    review = _review_projection(plan)
    assert len(json.dumps(review)) < 1024
    assert review['plan_id'] == plan['plan_id']
    assert review['selection']['role'] == plan['selection']['role']


@pytest.mark.parametrize('action', ['package-review', 'package-repair-review'])
def test_package_review_routes_return_compact_plan(monkeypatch, tmp_path, action):
    from odessy.setup import maintenance, native_bridge, package_context

    plan = {'plan_id': 'same-full-plan-hash', 'selection': {'role': 'combined'},
            'previous_state': {'large': 'x' * (3 * 1024 * 1024)},
            'payload_manifest': {'large': 'x' * (2 * 1024 * 1024)}}

    class Manager:
        def __init__(self, *args, **kwargs): pass
        def plan(self, *args): return plan
        def record(self): return {'selection': {'role': 'combined'}}

    monkeypatch.setattr(maintenance, 'ManagedInstallation', Manager)
    monkeypatch.setattr(package_context, 'context', lambda **kwargs: {
        'root': str(tmp_path / 'App'), 'payload_root': str(tmp_path),
        'bundle': str(tmp_path / 'wheelhouse')})
    response = native_bridge.manage({'action': action, 'root': str(tmp_path / 'App'),
                                     'selection': {'role': 'combined'}})
    assert len(json.dumps(plan)) > 4 * 1024 * 1024
    assert len(json.dumps(response)) < 1024
    assert response['plan_id'] == plan['plan_id']
