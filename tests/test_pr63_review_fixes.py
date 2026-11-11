import json
from pathlib import Path

import pytest


def test_registration_rejects_traversal_before_cache_creation(tmp_path, monkeypatch):
    from odessy.setup import windows_installation as w
    payload = tmp_path/'payload'; payload.mkdir()
    (payload/'setup-context.json').write_text(json.dumps({'files': {'../../escape': 'a'*64}}))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path/'local'))
    with pytest.raises(ValueError, match='path'):
        w._cache_helper(tmp_path/'app', {'id':'b'*32}, payload)
    assert not (tmp_path/'local').exists()


def test_harness_repair_restores_parent_before_nested_component():
    from installer.operations import maintenance
    assert maintenance._components({'components':['harness-runtime','runtime','application']})[:2] == ['runtime','harness-runtime']


def test_harness_tool_review_honors_hook_denial(tmp_path, monkeypatch):
    from odessy.runtime import hooks
    from odessy.tools.stdio_server import review
    from odessy.authority.policy import OdessyPolicy
    class DenyHooks:
        async def pre_tool(self, *args, **kwargs):
            return hooks.HookVerdict('deny', 'blocked by hook', 'test-hook')
        async def pre_escalation(self, *args, **kwargs):
            return hooks.ALLOW
    monkeypatch.setattr(hooks, 'load_hooks', lambda *a, **k: DenyHooks())
    decision, verdict = review(OdessyPolicy(tmp_path), 'time.now', {})
    assert decision.action == 'deny'
    assert 'blocked by hook' in verdict
