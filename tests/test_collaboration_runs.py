import asyncio
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from odessy.authority.action_approval import build_action_approval
from odessy.authority.policy import OdessyPolicy
from odessy.collaboration.context import collaboration_scope
from odessy.collaboration.jobs import JobManager
from odessy.collaboration.runs import HANDOFF_DIR, RunManager
from odessy.application.session import SessionController
from odessy.runtime.types import ToolRequest


def profile(path, name, adapter='codex'):
    return SimpleNamespace(name=name, adapter=adapter, model=None, provider='x', host=None, workspace=str(path),
                           command=(), credential_env=None, allowed_modes=('advice', 'patch'),
                           capabilities=('delegate',), timeout=30)


def git(path, *args):
    subprocess.run(['git', '-C', str(path), '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args],
                   check=True, capture_output=True)


@pytest.fixture
def repo(isolated_patch_repo):
    subprocess.run(['git', 'init', str(isolated_patch_repo)], check=True, capture_output=True)
    (isolated_patch_repo / 'app.py').write_text('value = 1\n')
    git(isolated_patch_repo, 'add', '.')
    git(isolated_patch_repo, 'commit', '-m', 'first')
    return isolated_patch_repo


def managers(repo, runner):
    profiles = {'writer': profile(repo, 'writer', 'claude_code'), 'coder': profile(repo, 'coder')}
    jobs = JobManager(profiles, runner=runner)
    return jobs, RunManager(jobs)


async def settled(runs, run_id, *states):
    for _ in range(500):
        run = runs.get(run_id)
        if run['state'] in states:
            return run
        await asyncio.sleep(.01)
    raise AssertionError(f'run stayed in {run["state"]}')


def document(text):
    return {'returncode': 0, 'output': '{"type":"result","result":' + repr(text).replace("'", '"') + '}'}


DOCUMENT = ('document_to_code', 'writer', 'write the spec', 'coder', 'implement it')
REVIEW = ('code_to_review', 'coder', 'change it', 'writer', 'review it')


def test_document_reaches_the_second_agent_as_a_file_and_stays_out_of_the_diff(repo):
    async def run():
        seen = []
        async def runner(command, *, cwd, env, on_output):
            seen.append((command, Path(cwd)))
            if len(seen) == 1:
                return document('The spec')
            handoff = Path(cwd) / HANDOFF_DIR / 't1.md'
            assert 'The spec' in handoff.read_text(encoding='utf-8')
            (Path(cwd) / 'app.py').write_text('value = 2\n')
            return {'returncode': 0, 'output': ''}
        jobs, runs = managers(repo, runner)
        started = await runs.start(*DOCUMENT, approved=True)
        waiting = await settled(runs, started['run_id'], 'awaiting_handoff')
        artifact = waiting['transfers'][0]['artifact']
        assert artifact['kind'] == 'document' and artifact['preview'] == 'The spec'
        assert waiting['steps'][1]['job_id'] is None
        assert runs.artifact(started['run_id'])['text'] == 'The spec'
        await runs.continue_run(started['run_id'], artifact['sha256'], approved=True)
        done = await settled(runs, started['run_id'], 'completed')
        assert done['transfers'][0]['state'] == 'delivered'
        assert done['steps'][1]['changed_files'] == ['app.py']
        assert jobs.get(done['steps'][1]['job_id'])['diff'].count('diff --git') == 1
        assert seen[0][1] == seen[1][1] == Path(done['worktree'])
        assert 'HANDOFF' not in seen[1][0][-1] and f'{HANDOFF_DIR}/t1.md' in ' '.join(seen[1][0])
        assert (repo / 'app.py').read_text() == 'value = 1\n'
    asyncio.run(run())


def test_code_reaches_the_reviewer_in_the_same_worktree(repo):
    async def run():
        seen = []
        async def runner(command, *, cwd, env, on_output):
            seen.append(Path(cwd))
            if len(seen) == 1:
                (Path(cwd) / 'app.py').write_text('value = 2\n')
                return {'returncode': 0, 'output': ''}
            assert (Path(cwd) / 'app.py').read_text() == 'value = 2\n'
            assert '+value = 2' in (Path(cwd) / HANDOFF_DIR / 't1.md').read_text(encoding='utf-8')
            return document('Looks fine')
        _, runs = managers(repo, runner)
        run_id = (await runs.start(*REVIEW, approved=True))['run_id']
        waiting = await settled(runs, run_id, 'awaiting_handoff')
        assert waiting['transfers'][0]['artifact']['changed_files'] == ['app.py']
        await runs.continue_run(run_id, approved=True)
        done = await settled(runs, run_id, 'completed')
        assert done['steps'][1]['result'] == 'Looks fine'
    asyncio.run(run())


def test_handoff_needs_the_hash_that_was_offered_and_an_unchanged_worktree(repo):
    async def run():
        calls = []
        async def runner(*args, **kwargs):
            calls.append(args)
            return document('The spec')
        _, runs = managers(repo, runner)
        run_id = (await runs.start(*DOCUMENT, approved=True))['run_id']
        waiting = await settled(runs, run_id, 'awaiting_handoff')
        with pytest.raises(ValueError, match='approval'):
            await runs.continue_run(run_id)
        with pytest.raises(ValueError, match='does not match'):
            await runs.continue_run(run_id, 'f' * 64, approved=True)
        assert runs.get(run_id)['state'] == 'awaiting_handoff'
        (Path(waiting['worktree']) / 'app.py').write_text('tampered\n')
        with pytest.raises(ValueError, match='changed'):
            await runs.continue_run(run_id, approved=True)
        assert runs.get(run_id)['state'] == 'needs_attention'
        assert len(calls) == 1
    asyncio.run(run())


def test_committed_changes_during_the_pause_are_refused(repo):
    async def run():
        async def runner(*args, **kwargs):
            return document('The spec')
        _, runs = managers(repo, runner)
        run_id = (await runs.start(*DOCUMENT, approved=True))['run_id']
        waiting = await settled(runs, run_id, 'awaiting_handoff')
        tree = Path(waiting['worktree'])
        (tree / 'app.py').write_text('unreviewed change\n')
        git(tree, 'add', '.')
        git(tree, '-c', 'commit.gpgsign=false', 'commit', '-m', 'changed while paused')
        with pytest.raises(ValueError, match='changed'):
            await runs.continue_run(run_id, approved=True)
        assert runs.get(run_id)['state'] == 'needs_attention'
    asyncio.run(run())


def test_binary_changes_during_the_pause_are_refused(repo):
    (repo / 'data.bin').write_bytes(b'\0baseline')
    git(repo, 'add', '.')
    git(repo, '-c', 'commit.gpgsign=false', 'commit', '-m', 'binary fixture')
    async def run():
        async def runner(command, *, cwd, **kwargs):
            (Path(cwd) / 'data.bin').write_bytes(b'\0first')
            return document('changed')
        _, runs = managers(repo, runner)
        run_id = (await runs.start(*REVIEW, approved=True))['run_id']
        waiting = await settled(runs, run_id, 'awaiting_handoff')
        (Path(waiting['worktree']) / 'data.bin').write_bytes(b'\0second')
        with pytest.raises(ValueError, match='changed'):
            await runs.continue_run(run_id, approved=True)
    asyncio.run(run())


@pytest.mark.parametrize('symlink', [False, True])
def test_preexisting_handoff_paths_are_not_overwritten(repo, symlink):
    async def run():
        async def runner(*args, **kwargs):
            return document('The spec')
        _, runs = managers(repo, runner)
        run_id = (await runs.start(*DOCUMENT, approved=True))['run_id']
        waiting = await settled(runs, run_id, 'awaiting_handoff')
        target = repo / 'outside.txt'
        target.write_text('keep me')
        directory = Path(waiting['worktree']) / HANDOFF_DIR
        directory.mkdir()
        path = directory / 't1.md'
        if symlink:
            try:
                os.symlink(target, path)
            except OSError:
                await runs.close()
                pytest.skip('Creating file symlinks requires OS permission')
        else:
            path.write_text('keep me')
        with pytest.raises(ValueError):
            await runs.continue_run(run_id, approved=True)
        assert target.read_text() == 'keep me'
        assert path.read_text() == 'keep me'
        assert runs.get(run_id)['steps'][1]['job_id'] is None
    asyncio.run(run())


def test_read_only_step_cannot_hide_changes_in_a_commit(repo):
    async def run():
        async def runner(command, *, cwd, **kwargs):
            tree = Path(cwd)
            (tree / 'app.py').write_text('changed and committed\n')
            git(tree, 'add', '.')
            git(tree, '-c', 'commit.gpgsign=false', 'commit', '-m', 'hidden write')
            return document('The spec')
        _, runs = managers(repo, runner)
        run_id = (await runs.start(*DOCUMENT, approved=True))['run_id']
        result = await settled(runs, run_id, 'awaiting_handoff', 'needs_attention')
        assert result['state'] == 'needs_attention'
    asyncio.run(run())


@pytest.mark.parametrize('result, state', [
    ({'returncode': 1, 'output': 'boom'}, 'failed'),
    (document(''), 'needs_attention'),
])
def test_a_first_step_without_output_hands_nothing_over_and_frees_the_repository(repo, result, state):
    async def run():
        async def runner(*args, **kwargs):
            return result
        jobs, runs = managers(repo, runner)
        run_id = (await runs.start(*DOCUMENT, approved=True))['run_id']
        done = await settled(runs, run_id, state)
        assert done['transfers'][0]['artifact'] is None
        job = await jobs.submit('coder', 'the repository is free again')
        await jobs.wait(job['job_id'])
    asyncio.run(run())


def test_read_only_steps_that_change_files_need_attention(repo):
    async def run():
        async def runner(command, *, cwd, env, on_output):
            (Path(cwd) / 'app.py').write_text('changed by the writer\n')
            return document('The spec')
        _, runs = managers(repo, runner)
        run_id = (await runs.start(*DOCUMENT, approved=True))['run_id']
        done = await settled(runs, run_id, 'needs_attention')
        assert 'read-only' in done['error']
    asyncio.run(run())


def test_review_that_changes_files_needs_attention(repo):
    async def run():
        count = []
        async def runner(command, *, cwd, env, on_output):
            count.append(1)
            (Path(cwd) / 'app.py').write_text(f'value = {len(count) + 1}\n')
            return document('done')
        _, runs = managers(repo, runner)
        run_id = (await runs.start(*REVIEW, approved=True))['run_id']
        await settled(runs, run_id, 'awaiting_handoff')
        await runs.continue_run(run_id, approved=True)
        done = await settled(runs, run_id, 'needs_attention')
        assert 'review changed files' in done['error']
    asyncio.run(run())


def test_the_repository_is_held_while_waiting_and_freed_by_declining(repo):
    async def run():
        async def runner(*args, **kwargs):
            return document('The spec')
        jobs, runs = managers(repo, runner)
        run_id = (await runs.start(*DOCUMENT, approved=True))['run_id']
        await settled(runs, run_id, 'awaiting_handoff')
        with pytest.raises(ValueError, match='active job'):
            await jobs.submit('coder', 'another job')
        assert runs.decline(run_id)['state'] == 'declined'
        with pytest.raises(ValueError, match='not waiting'):
            await runs.continue_run(run_id, approved=True)
        job = await jobs.submit('coder', 'another job')
        await jobs.wait(job['job_id'])
    asyncio.run(run())


def test_cancelling_stops_the_running_step_and_frees_the_repository(repo):
    async def run():
        blocked = asyncio.Event()
        async def runner(*args, **kwargs):
            await blocked.wait()
        jobs, runs = managers(repo, runner)
        run_id = (await runs.start(*DOCUMENT, approved=True))['run_id']
        cancelled = await runs.cancel(run_id)
        assert cancelled['state'] == 'cancelled' and cancelled['steps'][0]['state'] == 'cancelled'
        assert Path(cancelled['worktree']).is_dir()
        await runs.close()
        await jobs.close()
    asyncio.run(run())


def test_a_run_needs_approval_a_clean_repository_and_a_known_flow(repo):
    async def run():
        async def runner(*args, **kwargs):
            return document('x')
        jobs, runs = managers(repo, runner)
        with pytest.raises(ValueError, match='approval'):
            await runs.start(*DOCUMENT)
        with pytest.raises(ValueError, match='flow'):
            await runs.start('anything', 'writer', 'a', 'coder', 'b', approved=True)
        (repo / 'stray.txt').write_text('x')
        with pytest.raises(ValueError, match='clean git'):
            await runs.start(*DOCUMENT, approved=True)
        (repo / 'stray.txt').unlink()
        # The refused start released the repository.
        await settled(runs, (await runs.start(*DOCUMENT, approved=True))['run_id'], 'awaiting_handoff')
    asyncio.run(run())


def test_remote_participants_and_missing_modes_are_refused(repo):
    remote = profile(repo, 'far')
    remote.host = 'laptop'
    read_only = profile(repo, 'reader')
    read_only.allowed_modes = ('advice',)
    runs = RunManager(JobManager({'writer': profile(repo, 'writer'), 'far': remote, 'reader': read_only},
                                 remote_targets={'laptop': object()}))
    with pytest.raises(ValueError, match='local only'):
        runs.preflight_start('document_to_code', 'writer', 'a', 'far', 'b')
    with pytest.raises(ValueError, match='mode'):
        runs.preflight_start('document_to_code', 'writer', 'a', 'reader', 'b')


def service_with(repo):
    settings = SimpleNamespace(enabled=True, default_participant='writer', automatic=True)
    participants = {'writer': profile(repo, 'writer'), 'reader': profile(repo, 'reader')}
    participants['reader'].allowed_modes = ('advice',)
    return SimpleNamespace(config=SimpleNamespace(settings=settings, participants=participants))


def test_policy_confirms_runs_at_high_risk_and_checks_each_participant(repo):
    policy = OdessyPolicy(workspace_root=repo)
    arguments = {'flow': 'document_to_code', 'from_participant': 'reader', 'from_task': 'a',
                 'to_participant': 'writer', 'to_task': 'b'}
    with collaboration_scope(service_with(repo)):
        decision = policy.evaluate(ToolRequest('agent.run', arguments))
        assert (decision.action, decision.risk) == ('confirm', 'high')
        assert policy.evaluate(ToolRequest('agent.run_continue', {})).action == 'confirm'
        assert policy.evaluate(ToolRequest('agent.run_status', {})).action == 'allow'
        swapped = {**arguments, 'from_participant': 'writer', 'to_participant': 'reader'}
        assert policy.evaluate(ToolRequest('agent.run', swapped)).action == 'deny'
        assert policy.evaluate(ToolRequest('agent.run', {**arguments, 'flow': 'loop'})).action == 'deny'
        assert policy.evaluate(ToolRequest('agent.run', {**arguments, 'to_participant': 'nobody'})).action == 'deny'
    assert policy.evaluate(ToolRequest('agent.run', arguments)).rule == 'collaboration_off'


def test_the_approval_shows_and_binds_the_output_being_handed_over(repo):
    async def run():
        async def runner(*args, **kwargs):
            return document('The spec')
        _, runs = managers(repo, runner)
        run_id = (await runs.start(*DOCUMENT, approved=True))['run_id']
        await settled(runs, run_id, 'awaiting_handoff')
        target = runs.preflight_continue(run_id)
        policy = OdessyPolicy(workspace_root=repo)
        request = ToolRequest('agent.run_continue', {'run_id': run_id})
        with collaboration_scope(service_with(repo)):
            decision = policy.evaluate(request)
            plan = build_action_approval(request, decision, policy, call_id='c', execution_target=target)
            assert target['artifact_sha256'] in plan.authoritative_prompt and 'coder' in plan.authoritative_prompt
            assert plan.public_dict()['target_identity']['artifact_preview'] == 'The spec'
            assert 'voice' not in plan.allowed_channels
            other = {**target, 'artifact_sha256': 'f' * 64}
            assert not plan.matches(request, decision, policy, execution_target=other)
    asyncio.run(run())


def test_refusing_the_approval_declines_the_run(repo):
    async def run():
        async def runner(*args, **kwargs):
            return document('The spec')
        _, runs = managers(repo, runner)
        run_id = (await runs.start(*DOCUMENT, approved=True))['run_id']
        await settled(runs, run_id, 'awaiting_handoff')
        controller = SimpleNamespace(_stop=asyncio.Event(), _closed=False, runs=runs,
                                     interactions=SimpleNamespace(request_approval=None))
        async def refuse(details):
            return False
        controller.interactions.request_approval = refuse
        details = {'operation': 'agent.run_continue', 'target_identity': {'run_id': run_id}}
        assert await SessionController.confirm_action(controller, details) is False
        assert runs.get(run_id)['state'] == 'declined'
    asyncio.run(run())


def real_controller(repo, runner):
    jobs, runs = managers(repo, runner)
    controller = SessionController(model_factory=lambda name: None, agent_factory=lambda *a, **kw: None,
                                   profiles={'local': {}}, jobs=jobs)
    controller.runs = runs
    return controller, runs


async def answer(controller, name, arguments, approved):
    pending = asyncio.create_task(controller.confirm({'risk': 'high'}, ToolRequest(name, arguments)))
    for _ in range(100):
        await asyncio.sleep(.01)
        if controller.pending_approvals:
            break
    approval = controller.pending_approvals[0]
    assert approval['operation'] == name and approval['risk'] == 'high'
    controller.answer_approval(approval['id'], approved)
    return await pending, approval


def test_direct_approvals_preflight_and_bind_both_decisions(repo):
    async def run():
        async def runner(*args, **kwargs):
            return document('The spec')
        controller, runs = real_controller(repo, runner)
        arguments = dict(zip(('flow', 'from_participant', 'from_task', 'to_participant', 'to_task'), DOCUMENT))
        with pytest.raises(ValueError, match='flow'):
            await controller.confirm({'risk': 'high'}, ToolRequest('agent.run', {**arguments, 'flow': 'loop'}))
        assert not controller.pending_approvals
        approved, approval = await answer(controller, 'agent.run', arguments, True)
        assert approved and approval['target_identity']['flow'] == 'document_to_code'
        run_id = (await runs.start(**arguments, approved=True))['run_id']
        waiting = await settled(runs, run_id, 'awaiting_handoff')
        sha = waiting['transfers'][0]['artifact']['sha256']
        with pytest.raises(ValueError, match='does not match'):
            await controller.confirm({'risk': 'high'}, ToolRequest('agent.run_continue',
                {'run_id': run_id, 'artifact_sha256': 'f' * 64}))
        approved, approval = await answer(controller, 'agent.run_continue',
                                          {'run_id': run_id, 'artifact_sha256': sha}, False)
        assert not approved and approval['target_identity']['artifact_sha256'] == sha
        assert runs.get(run_id)['state'] == 'declined'
        await controller.close()
    asyncio.run(run())


def test_runs_api_passes_each_decision_through_an_approval(repo):
    async def run():
        # fastapi and httpx live in the optional `[ui]` extra.
        pytest.importorskip('fastapi')
        httpx = pytest.importorskip('httpx')
        from odessy.application.api import create_app
        async def runner(*args, **kwargs):
            return document('The spec')
        controller, runs = real_controller(repo, runner)
        app = create_app(lambda: controller, token='test-secret')
        async def approve_next():
            while not controller.pending_approvals:
                await asyncio.sleep(.01)
            controller.answer_approval(controller.pending_approvals[0]['id'], True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                headers={'Authorization': 'Bearer test-secret'}) as client:
            route = '/v1/sessions/' + (await client.post('/v1/sessions')).json()['id'] + '/runs'
            assert (await client.post(route, json={'flow': 'document_to_code'})).status_code == 400
            body = dict(zip(('flow', 'from_participant', 'from_task', 'to_participant', 'to_task'), DOCUMENT))
            started, _ = await asyncio.gather(client.post(route, json=body), approve_next())
            assert started.status_code == 202
            run_id = started.json()['run_id']
            await settled(runs, run_id, 'awaiting_handoff')
            artifact = (await client.get(f'{route}/{run_id}/artifact')).json()
            assert artifact['text'] == 'The spec'
            assert (await client.post(f'{route}/{run_id}/continue', json={})).status_code == 400
            assert (await client.get(route + '/missing')).status_code == 404
            continued, _ = await asyncio.gather(
                client.post(f'{route}/{run_id}/continue', json={'artifact_sha256': artifact['sha256']}), approve_next())
            assert continued.status_code == 200
            await settled(runs, run_id, 'completed')
        await controller.close()
    asyncio.run(run())


def test_run_events_carry_the_snapshot_a_panel_draws(repo):
    async def run():
        async def runner(command, *, cwd, env, on_output):
            on_output('working\n')
            return document('The spec')
        events = []
        _, runs = managers(repo, runner)
        runs.event_sink = events.append
        run_id = (await runs.start(*DOCUMENT, approved=True))['run_id']
        await settled(runs, run_id, 'awaiting_handoff')
        assert all(event['type'] == 'collaboration.run' and event['run_id'] == run_id for event in events)
        assert any(event['steps'][0]['progress'] == ['working'] for event in events)
        last = events[-1]
        assert last['state'] == 'awaiting_handoff'
        assert last['transfers'][0]['from'] == 's1' and last['transfers'][0]['to'] == 's2'
        assert 'text' not in last['transfers'][0]['artifact']
    asyncio.run(run())


def test_latest_run_is_the_default_and_unknown_runs_are_reported(repo):
    async def run():
        async def runner(*args, **kwargs):
            return document('The spec')
        _, runs = managers(repo, runner)
        with pytest.raises(KeyError):
            runs.get()
        run_id = (await runs.start(*DOCUMENT, approved=True))['run_id']
        assert runs.get()['run_id'] == run_id
        with pytest.raises(KeyError):
            runs.get('missing')
        await settled(runs, run_id, 'awaiting_handoff')
    asyncio.run(run())
