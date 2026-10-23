import asyncio
import subprocess
from types import SimpleNamespace
import sys

import pytest

from odessy.collaboration.jobs import JobManager


def profile(path, **kw):
    return SimpleNamespace(name='coder', adapter='codex', model='m', provider='openai',
                           host=None, workspace=str(path), command=(), credential_env='TEST_JOB_KEY',
                           allowed_modes=('advice', 'patch'), capabilities=('delegate', 'consult'), **kw)


async def finished(manager, job):
    for _ in range(200):
        state = manager.get(job['job_id'])
        if state['state'] not in ('queued', 'running'):
            return state
        await asyncio.sleep(.01)
    raise AssertionError('job did not finish')


def test_advice_is_structured_redacted_and_environment_minimized(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setenv('TEST_JOB_KEY', 'secret-value')
        monkeypatch.setenv('UNRELATED_API_KEY', 'private')
        seen = {}
        async def runner(command, **kwargs):
            seen.update(command=command, **kwargs)
            kwargs['on_output']('secret-value')
            return {'returncode': 0, 'output': '{"type":"result","result":"ok"}'}
        manager = JobManager({'coder': profile(tmp_path)}, runner=runner)
        result = await finished(manager, await manager.submit('coder', 'review'))
        assert result['state'] == 'completed'
        assert '--json' in seen['command'] and 'read-only' in seen['command']
        assert 'UNRELATED_API_KEY' not in seen['env']
        assert 'secret-value' not in str(result)
        assert result['usage']['cost'] is None
        assert result['permissions']['enforcement'] == 'child_cli_not_verified'
        assert result['permissions']['requested_sandbox'] == 'read-only'
        await manager.close()
    asyncio.run(run())


def test_submission_authorization_workspace_and_repo_lock(tmp_path):
    async def run():
        wait = asyncio.Event()
        async def runner(*args, **kwargs):
            await wait.wait()
            return {'returncode': 0}
        manager = JobManager({'coder': profile(tmp_path)}, runner=runner)
        with pytest.raises(ValueError, match='approval'):
            await manager.submit('coder', 'change', mode='patch')
        with pytest.raises(ValueError, match='workspace'):
            await manager.submit('coder', 'read', workspace=tmp_path.parent)
        job = await manager.submit('coder', 'review')
        with pytest.raises(ValueError, match='active'):
            await manager.submit('coder', 'again')
        await manager.cancel(job['job_id'])
        assert manager.get(job['job_id'])['state'] == 'cancelled'
        await manager.close()
    asyncio.run(run())


def test_patch_collects_diff_without_touching_original_and_refuses_dirty_repo(tmp_path):
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=T', '-c', 'user.email=t@t', 'commit', '--allow-empty', '-m', 'initial'], check=True, capture_output=True)
    async def run():
        async def runner(command, **kwargs):
            (kwargs['cwd'] / 'new.txt').write_text('new content\n')
            return {'returncode': 0}
        manager = JobManager({'coder': profile(tmp_path)}, runner=runner)
        result = await finished(manager, await manager.submit('coder', 'change', mode='patch', approved=True))
        assert result['state'] == 'completed'
        assert 'new content' in result['diff'] and result['changed_files'] == ['new.txt']
        assert result['validation']['status'] == 'unknown'
        assert not (tmp_path / 'new.txt').exists()
        assert result['worktree']
        (tmp_path / 'dirty').write_text('dirty')
        with pytest.raises(ValueError, match='clean'):
            await manager.submit('coder', 'change', mode='patch', approved=True)
        await manager.close()
    asyncio.run(run())


def test_unknown_remote_never_runs_locally(tmp_path):
    async def run():
        p = profile(tmp_path)
        p.host = 'missing'
        manager = JobManager({'coder': p})
        with pytest.raises(ValueError, match='target'):
            await manager.submit('coder', 'review')
    asyncio.run(run())


def test_consult_adapter_parses_structured_result_and_cleans_up(tmp_path, monkeypatch):
    from odessy.collaboration.jobs import consult_agent
    async def runner(self, command, **kwargs):
        assert 'read-only' in command
        assert 'Context: relevant code' in command[-1]
        return {'returncode': 0, 'output': '{"type":"item.completed","item":{"type":"agent_message","text":"reviewed"}}'}
    monkeypatch.setattr(JobManager, '_subprocess', runner)
    assert consult_agent(profile(tmp_path), 'review?', 'relevant code') == 'reviewed'


def test_authorized_remote_dispatch_is_structured(tmp_path):
    async def run():
        calls = []
        async def target(action, payload):
            calls.append((action, payload))
            return {'state': 'completed', 'result': 'remote advice'}
        p = profile(tmp_path)
        p.host = 'remote'
        manager = JobManager({'coder': p}, remote_targets={'remote': target})
        result = await finished(manager, await manager.submit('coder', 'review'))
        assert result['result'] == 'remote advice'
        assert calls[0][0] == 'start' and calls[0][1]['mode'] == 'advice'
        assert 'command' not in calls[0][1]
        await manager.close()
    asyncio.run(run())


def test_custom_subprocess_is_cancellable_and_preserves_partial_output(tmp_path):
    async def run():
        p = profile(tmp_path)
        p.adapter = 'custom_agent'
        p.command = (sys.executable, '-u', '-c', 'import time; print("started",flush=True); time.sleep(60)')
        manager = JobManager({'coder': p})
        job = await manager.submit('coder', 'review')
        for _ in range(200):
            if 'started' in manager.get(job['job_id'])['log']:
                break
            await asyncio.sleep(.01)
        await asyncio.wait_for(manager.cancel(job['job_id']), 5)
        assert manager.get(job['job_id'])['state'] == 'cancelled'
        assert 'started' in manager.get(job['job_id'])['log']
        await manager.close()
    asyncio.run(run())


def test_jobs_require_delegate_and_consult_requires_consult(tmp_path):
    from odessy.collaboration.jobs import consult_agent
    p = profile(tmp_path)
    p.capabilities = ('consult',)
    async def run():
        manager = JobManager({'coder': p})
        with pytest.raises(ValueError, match='delegate'):
            await manager.submit('coder', 'review')
    asyncio.run(run())
    p.capabilities = ('delegate',)
    with pytest.raises(ValueError, match='consult'):
        consult_agent(p, 'review')


def test_remote_uses_remote_job_identity_and_collects_artifacts(tmp_path):
    async def run():
        calls = []
        async def target(action, payload):
            calls.append((action, payload))
            if action == 'start':
                return {'job_id': 'remote-123', 'state': 'running'}
            return {'job_id': 'remote-123', 'state': 'completed', 'diff': 'remote diff',
                    'changed_files': ['x'], 'validation': {'status': 'unknown'}, 'result': 'done'}
        p = profile(tmp_path)
        p.host = 'remote'
        manager = JobManager({'coder': p}, remote_targets={'remote': target})
        result = await finished(manager, await manager.submit('coder', 'review'))
        assert calls[1] == ('status', {'job_id': 'remote-123'})
        assert result['diff'] == 'remote diff' and result['changed_files'] == ['x']
        await manager.close()
    asyncio.run(run())


def test_background_job_timeout_cancels_runner(tmp_path):
    async def run():
        cancelled = asyncio.Event()
        async def runner(*args, **kwargs):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()
        p = profile(tmp_path)
        p.timeout = .02
        manager = JobManager({'coder': p}, runner=runner)
        result = await finished(manager, await manager.submit('coder', 'review'))
        assert result['state'] == 'needs_attention' and 'timed out' in result['error']
        assert cancelled.is_set()
        await manager.close()
    asyncio.run(run())


def test_streaming_redaction_covers_credentials_split_between_chunks(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setenv('TEST_JOB_KEY', 'split-secret-value')
        events = []
        async def runner(command, **kwargs):
            kwargs['on_output']('output split-sec')
            kwargs['on_output']('ret-value done\n')
            return {'returncode': 0}
        manager = JobManager({'coder': profile(tmp_path)}, runner=runner, event_sink=events.append)
        result = await finished(manager, await manager.submit('coder', 'review'))
        assert 'split-sec' not in str(events)
        assert '[REDACTED]' in result['log'] and 'done' in result['log']
        await manager.close()
    asyncio.run(run())


def test_remote_start_obeys_timeout(tmp_path):
    async def run():
        async def target(action, payload):
            if action == 'start':
                await asyncio.sleep(60)
            return {'state': 'cancelled'}
        p = profile(tmp_path)
        p.host, p.timeout = 'remote', .02
        manager = JobManager({'coder': p}, remote_targets={'remote': target})
        result = await finished(manager, await manager.submit('coder', 'review'))
        assert result['state'] == 'needs_attention' and 'timed out' in result['error']
        await manager.close()
    asyncio.run(run())


def test_manager_remote_consult_uses_consult_capability_without_delegate(tmp_path):
    async def run():
        calls = []
        async def target(action, payload):
            calls.append((action, payload))
            return {'status': 'ok', 'answer': 'remote opinion'}
        p = profile(tmp_path)
        p.host, p.capabilities = 'remote', ('consult',)
        manager = JobManager({'coder': p}, remote_targets={'remote': target})
        assert await manager.consult(p, 'question', 'context') == 'remote opinion'
        assert calls == [('consult', {'participant': 'coder', 'question': 'question', 'context': 'context'})]
        await manager.close()
    asyncio.run(run())


def test_remote_workspace_is_opaque_until_remote_host_validation(tmp_path):
    async def run():
        calls = []
        async def target(action, payload):
            calls.append((action, payload))
            return {'job_id': 'remote', 'state': 'completed', 'result': 'done'}
        p = profile(tmp_path)
        p.host, p.workspace = 'linux', '/srv/team/project'
        manager = JobManager({'coder': p}, remote_targets={'linux': target})
        result = await finished(manager, await manager.submit('coder', 'review'))
        assert result['workspace'] == '/srv/team/project'
        assert calls[0][1]['workspace'] == '/srv/team/project'
        await manager.close()
    asyncio.run(run())


def test_unconfirmed_remote_cancel_keeps_lock_until_terminal_refresh(tmp_path):
    async def run():
        terminal = False
        async def target(action, payload):
            if action == 'start':
                return {'job_id': 'remote', 'state': 'running'}
            if action == 'cancel':
                raise ConnectionError('host unreachable')
            return {'job_id': 'remote', 'state': 'cancelled' if terminal else 'running'}
        p = profile(tmp_path)
        p.host = 'remote'
        manager = JobManager({'coder': p}, remote_targets={'remote': target})
        job = await manager.submit('coder', 'review')
        await asyncio.sleep(.02)
        await manager.cancel(job['job_id'])
        assert manager.get(job['job_id'])['state'] == 'needs_attention'
        with pytest.raises(ValueError, match='active'):
            await manager.submit('coder', 'another')
        terminal = True
        refreshed = await manager.refresh(job['job_id'])
        assert refreshed['state'] == 'cancelled'
        next_job = await manager.submit('coder', 'another')
        await manager.cancel(next_job['job_id'])
        await manager.close()
    asyncio.run(run())


@pytest.mark.parametrize('remote', [False, True])
def test_repository_reservations_span_job_managers(tmp_path, remote):
    async def run():
        async def runner(*args, **kwargs):
            await asyncio.sleep(60)
        async def target(action, payload):
            return {'job_id': 'remote', 'state': 'cancelled' if action == 'cancel' else 'running'}
        p = profile(tmp_path)
        if remote:
            p.host, p.workspace = 'remote', '/srv/' + tmp_path.name
        targets = {'remote': target} if remote else {}
        first = JobManager({'coder': p}, runner=runner, remote_targets=targets)
        second = JobManager({'coder': p}, runner=runner, remote_targets=targets)
        job = await first.submit('coder', 'review')
        await asyncio.sleep(.01)
        with pytest.raises(ValueError, match='active'):
            await second.submit('coder', 'another review')
        await first.cancel(job['job_id'])
        next_job = await second.submit('coder', 'next review')
        await second.cancel(next_job['job_id'])
        await first.close()
        await second.close()
    asyncio.run(run())


@pytest.mark.parametrize(('output', 'expected'), [
    ('{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":40,"output_tokens":10}}',
     {'input_tokens': 100, 'cached_input_tokens': 40, 'output_tokens': 10, 'cost': None}),
    ('{"type":"result","result":"ok","usage":{"input_tokens":100,"cache_read_input_tokens":40,"cache_creation_input_tokens":20,"output_tokens":10},"total_cost_usd":0.03}',
     {'input_tokens': 160, 'cached_input_tokens': 40, 'output_tokens': 10, 'cost': .03}),
])
def test_cli_usage_report_is_separate_from_api_ledger(tmp_path, output, expected):
    from odessy.models.usage import process_ledger
    async def run():
        async def runner(*args, **kwargs):
            return {'returncode': 0, 'output': output}
        manager = JobManager({'coder': profile(tmp_path)}, runner=runner)
        result = await finished(manager, await manager.submit('coder', 'review'))
        for field, value in expected.items():
            assert result['usage'][field] == value
        assert result['usage']['source'] == 'coding_cli'
        assert result['usage']['outside_api_budget'] is True
        assert process_ledger().total.calls == 0
        await manager.close()
    asyncio.run(run())


def test_runner_and_remote_usage_reports_are_validated(tmp_path):
    async def run():
        async def runner(*args, **kwargs):
            return {'returncode': 0, 'usage': {'input_tokens': 10, 'output_tokens': -3,
                    'cached_input_tokens': True, 'total_cost_usd': float('inf')}}
        manager = JobManager({'coder': profile(tmp_path)}, runner=runner)
        result = await finished(manager, await manager.submit('coder', 'review'))
        assert result['usage']['input_tokens'] == 10
        assert result['usage']['output_tokens'] is None and result['usage']['cost'] is None
        assert result['usage']['cached_input_tokens'] is None
        await manager.close()
        async def target(action, payload):
            return {'job_id': 'remote', 'state': 'completed', 'usage': {
                'input_tokens': 20, 'output_tokens': 4, 'cost': .1, 'currency': 'USD',
                'outside_api_budget': False, 'source': 'api'}}
        p = profile(tmp_path)
        p.host = 'remote'
        manager = JobManager({'coder': p}, remote_targets={'remote': target})
        result = await finished(manager, await manager.submit('coder', 'review'))
        assert result['usage']['input_tokens'] == 20 and result['usage']['cost'] == .1
        assert result['usage']['source'] == 'coding_cli' and result['usage']['outside_api_budget'] is True
        await manager.close()
    asyncio.run(run())
