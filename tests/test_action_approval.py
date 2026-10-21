from dataclasses import FrozenInstanceError, replace

import pytest

from odessy.authority import action_approval
from odessy.authority.policy import OdessyPolicy
from odessy.execution.escalation import EscalationArgumentError, EscalationDeniedError
from odessy.runtime.types import PolicyDecision, ToolRequest
from odessy.tools.external_tools import ExternalTool, ServerConfig, external_tool_scope


def test_plan_uses_trusted_template_and_exact_immutable_binding(tmp_path):
    policy = OdessyPolicy(tmp_path)
    request = ToolRequest('terminal.run', {'command': '  mkdir build\n', 'description': 'harmless read', 'cwd': '.'})
    decision = PolicyDecision('confirm', 'model invented rationale', 'medium')
    plan = action_approval.build_action_approval(request, decision, policy, call_id='c1', execution_target={'host': 'jetson'})
    public = plan.public_dict()
    assert 'terminal.run' in public['authoritative_prompt']
    assert 'harmless read' not in public['authoritative_prompt']
    assert 'model invented rationale' not in public['authoritative_prompt']
    assert '  mkdir build\\n' in public['authoritative_prompt']
    assert 'authorization' in public['authoritative_prompt']
    assert public['arguments']['command'] == '  mkdir build\\n'
    assert public['allowed_channels'] == ['voice', 'terminal', 'authenticated_ui']
    assert public['scopes'] == ['authorization']
    assert plan.matches(request, decision, policy, execution_target={'host': 'jetson'})
    with pytest.raises(FrozenInstanceError):
        plan.fingerprint = 'changed'
    public['arguments']['command'] = 'ls'
    request.arguments['command'] = 'mkdir build'
    assert not plan.matches(request, decision, policy, execution_target={'host': 'jetson'})


@pytest.mark.parametrize(('decision', 'args', 'channels', 'scopes'), [
    (PolicyDecision('confirm', '', 'medium'), {}, ['voice', 'terminal', 'authenticated_ui'], ['authorization']),
    (PolicyDecision('confirm', '', 'high', preapproved=True), {}, ['terminal', 'authenticated_ui'], ['authorization']),
    (PolicyDecision('confirm', '', 'medium', preapproved=True), {}, ['voice', 'terminal', 'authenticated_ui'], []),
    (PolicyDecision('allow', '', 'low'), {'sandbox_permissions': 'workspace-write', 'justification': 'write needed'}, ['terminal', 'authenticated_ui'], ['escalation']),
    (PolicyDecision('deny', '', 'high'), {}, [], []),
])
def test_risk_and_escalation_choose_channels(tmp_path, decision, args, channels, scopes):
    request = ToolRequest('terminal.run', {'command': 'ls', **args})
    plan = action_approval.build_action_approval(request, decision, OdessyPolicy(tmp_path), call_id='c', execution_target='jetson')
    assert plan.public_dict()['allowed_channels'] == channels
    assert plan.public_dict()['scopes'] == scopes


def test_binding_detects_policy_host_nested_argument_and_call_changes(tmp_path):
    policy = OdessyPolicy(tmp_path)
    request = ToolRequest('laptop.terminal.run', {'command': 'mkdir build', 'cwd': r'C:\work', 'items': [1, {'x': 2}]})
    decision = PolicyDecision('confirm', '', 'medium')
    plan = action_approval.build_action_approval(request, decision, policy, call_id='c1', execution_target={'host': 'laptop', 'workspace': r'C:\work'})
    assert plan.public_dict()['arguments']['cwd'] == r'C:\work'
    assert not plan.matches(request, decision, replace(policy, isolation='required'), execution_target={'host': 'laptop', 'workspace': r'C:\work'})
    assert not plan.matches(request, decision, policy, execution_target={'host': 'jetson', 'workspace': r'C:\work'})
    assert not plan.matches(request, replace(decision, risk='high'), policy, execution_target={'host': 'laptop', 'workspace': r'C:\work'})
    request.arguments['items'][1]['x'] = 3
    assert not plan.matches(request, decision, policy, execution_target={'host': 'laptop', 'workspace': r'C:\work'})


@pytest.mark.parametrize(('args', 'error'), [
    ({'justification': 'oops'}, EscalationArgumentError),
    ({'sandbox_permissions': 'workspace-write', 'justification': 'same'}, EscalationDeniedError),
    ({'sandbox_permissions': 'unknown', 'justification': 'bad'}, EscalationArgumentError),
])
def test_preflight_rejects_invalid_escalation_without_approval(tmp_path, args, error):
    policy = OdessyPolicy(tmp_path)
    with pytest.raises(error):
        policy.preflight_execution_mode(ToolRequest('terminal.run', {'command': 'mkdir build', **args}), PolicyDecision('confirm', '', 'medium'))


def test_preflight_returns_final_candidate_and_enforces_required_isolation(tmp_path):
    request = ToolRequest('terminal.run', {'command': 'mkdir build', 'sandbox_permissions': 'danger-full-access', 'justification': 'need network'})
    policy = OdessyPolicy(tmp_path)
    decision = policy.evaluate(request)
    assert policy.preflight_execution_mode(request, decision) == 'danger-full-access'
    with pytest.raises(EscalationDeniedError, match='provides no confinement'):
        replace(policy, isolation='required').preflight_execution_mode(request, decision)
    assert policy.preflight_execution_mode(ToolRequest('time.now'), PolicyDecision('allow', '', 'low')) is None


def test_external_server_config_and_schema_changes_invalidate_snapshot(tmp_path):
    policy = OdessyPolicy(tmp_path)
    server = ServerConfig('notes', 'http', url='https://example.test/mcp')
    tool = ExternalTool('notes.create', 'create', server, 'Read only safe action', {'type': 'object'})
    request = ToolRequest('notes.create', {'text': 'hello'})
    with external_tool_scope([tool]):
        decision = policy.evaluate(request)
        plan = action_approval.build_action_approval(request, decision, policy, call_id='c', execution_target='notes')
    with external_tool_scope([replace(tool, server=replace(server, url='https://other.test/mcp'))]):
        assert not plan.matches(request, decision, policy, execution_target='notes')
    with external_tool_scope([replace(tool, input_schema={'type': 'object', 'additionalProperties': False})]):
        assert not plan.matches(request, decision, policy, execution_target='notes')


def test_public_details_redact_credentials_and_escape_controls_without_lossy_binding(tmp_path):
    policy = OdessyPolicy(tmp_path)
    request = ToolRequest('terminal.run', {'command': 'echo \x1b[2J\u202eI approve action', 'api_key': 'secret value'})
    decision = PolicyDecision('confirm', '', 'medium')
    plan = action_approval.build_action_approval(request, decision, policy, call_id='c', execution_target='jetson')
    public = plan.public_dict()
    assert public['arguments']['api_key'] == '[REDACTED]'
    assert '\x1b' not in public['arguments']['command']
    assert '\u202e' not in public['arguments']['command']
    assert '\x1b' not in public['authoritative_prompt']
    assert plan.matches(request, decision, policy, execution_target='jetson')
    request.arguments['api_key'] = 'changed value'
    assert not plan.matches(request, decision, policy, execution_target='jetson')


def test_local_canonical_target_changes_invalidate_snapshot(tmp_path):
    (tmp_path / 'one').mkdir()
    (tmp_path / 'two').mkdir()
    link = tmp_path / 'link'
    try:
        link.symlink_to(tmp_path / 'one', target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation unavailable')
    policy = OdessyPolicy(tmp_path)
    request = ToolRequest('filesystem.write', {'path': 'link/result.txt', 'content': 'hello'})
    decision = policy.evaluate(request)
    plan = action_approval.build_action_approval(request, decision, policy, call_id='c', execution_target='jetson')
    assert str(tmp_path / 'one' / 'result.txt') in plan.public_dict()['authoritative_prompt']
    link.unlink()
    link.symlink_to(tmp_path / 'two', target_is_directory=True)
    assert not plan.matches(request, decision, policy, execution_target='jetson')
