"""Layer 3: N-step plan runs — multi-step handoff with per-step approval."""
import asyncio
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from odessy.collaboration._handoff_notes import HANDOFF_DIR, write_note
from odessy.collaboration.jobs import JobManager
from odessy.collaboration.plan_runs import PlanRunManager, step_spec


def profile(path, name):
    return SimpleNamespace(name=name, adapter='codex', model=None, provider='x', host=None,
                           workspace=str(path), command=(), credential_env=None,
                           allowed_modes=('advice', 'patch'), capabilities=('delegate',),
                           timeout=30, roles=())


@pytest.fixture
def repo(isolated_patch_repo):
    subprocess.run(['git', 'init', str(isolated_patch_repo)], check=True, capture_output=True)
    (isolated_patch_repo / 'app.py').write_text('value = 1\n')
    subprocess.run(['git', '-C', str(isolated_patch_repo), '-c', 'user.name=t', '-c', 'user.email=t@e.com',
                    'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(isolated_patch_repo), '-c', 'user.name=t', '-c', 'user.email=t@e.com',
                    'commit', '-m', 'first'], check=True, capture_output=True)
    return isolated_patch_repo


def managers(repo, runner):
    profiles = {'a': profile(repo, 'a'), 'b': profile(repo, 'b'), 'c': profile(repo, 'c')}
    jobs = JobManager(profiles, runner=runner)
    return jobs, PlanRunManager(jobs)


def document(text):
    return {'returncode': 0, 'output': '{"type":"result","result":' + repr(text).replace("'", '"') + '}'}


async def settled(manager, run_id, *states):
    for _ in range(500):
        run = manager.get(run_id)
        if run['state'] in states:
            return run
        await asyncio.sleep(.01)
    raise AssertionError(f'run stayed in {run["state"]}')


THREE_STEPS = [
    step_spec('requirements', 'a', 'write the spec', 'advice'),
    step_spec('implementation', 'b', 'implement it', 'patch'),
    step_spec('review', 'c', 'review it', 'advice'),
]


def test_three_step_plan_carries_each_output_forward(repo):
    async def run():
        seen = []
        async def runner(command, *, cwd, env, on_output):
            seen.append((list(Path(cwd).glob(f'{HANDOFF_DIR}/*.md')), Path(cwd)))
            count = len(seen)
            if count == 1:
                return document('The spec')
            if count == 2:
                assert any('The spec' in p.read_text(encoding='utf-8') for p in seen[-1][0])
                (Path(cwd) / 'app.py').write_text('value = 2\n')
                return {'returncode': 0, 'output': ''}
            # step 3 (review): sees both handoff files and the code change
            assert any('The spec' in p.read_text(encoding='utf-8') for p in seen[-1][0])
            assert (Path(cwd) / 'app.py').read_text() == 'value = 2\n'
            return document('Looks fine')
        _, manager = managers(repo, runner)
        started = await manager.start_plan(THREE_STEPS, approved=True)
        run_id = started['run_id']
        assert len(started['steps']) == 3 and len(started['transfers']) == 2

        # first handoff
        waiting = await settled(manager, run_id, 'awaiting_handoff')
        assert waiting['transfer_index'] == 0
        await manager.continue_plan(run_id, waiting['transfers'][0]['artifact']['sha256'], approved=True)

        # second handoff
        waiting2 = await settled(manager, run_id, 'awaiting_handoff')
        assert waiting2['transfer_index'] == 1
        assert waiting2['transfers'][0]['state'] == 'delivered'
        await manager.continue_plan(run_id, waiting2['transfers'][1]['artifact']['sha256'], approved=True)

        done = await settled(manager, run_id, 'completed')
        assert [s['state'] for s in done['steps']] == ['completed'] * 3
        assert [t['state'] for t in done['transfers']] == ['delivered', 'delivered']
        assert done['steps'][2]['result'] == 'Looks fine'
        assert (repo / 'app.py').read_text() == 'value = 1\n'  # source untouched
    asyncio.run(run())


@pytest.mark.parametrize('has_existing_note', [False, True])
def test_handoff_writer_refuses_directory_links(tmp_path, has_existing_note):
    worktree = tmp_path / 'worktree'
    worktree.mkdir()
    notes = worktree / HANDOFF_DIR
    elsewhere = tmp_path / 'other-directory'
    elsewhere.mkdir()
    if has_existing_note:
        write_note(worktree, 't1.md', 'First handoff')
        notes.rename(tmp_path / 'previous-notes')
    if os.name == 'nt':
        # A junction needs no symlink privilege on Windows.
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(notes), str(elsewhere)],
                       check=True, capture_output=True)
    else:
        notes.symlink_to(elsewhere, target_is_directory=True)
    try:
        with pytest.raises(OSError):
            write_note(worktree, 't2.md' if has_existing_note else 't1.md', 'Fixture note')
        assert list(elsewhere.iterdir()) == []
    finally:
        if os.name == 'nt':
            notes.rmdir()
        else:
            notes.unlink()


def test_each_handoff_bound_to_its_own_hash(repo):
    async def run():
        async def runner(*args, **kwargs):
            return document('step output')
        _, manager = managers(repo, runner)
        run_id = (await manager.start_plan(THREE_STEPS, approved=True))['run_id']
        waiting = await settled(manager, run_id, 'awaiting_handoff')
        with pytest.raises(ValueError, match='does not match'):
            await manager.continue_plan(run_id, 'wrong-hash', approved=True)
        assert manager.get(run_id)['state'] == 'awaiting_handoff'
    asyncio.run(run())


def test_readonly_step_changing_files_goes_needs_attention(repo):
    async def run():
        count = [0]
        async def runner(command, *, cwd, env, on_output):
            count[0] += 1
            (Path(cwd) / 'app.py').write_text('mutated\n')  # even the advice step writes
            return document('output')
        _, manager = managers(repo, runner)
        run_id = (await manager.start_plan(THREE_STEPS, approved=True))['run_id']
        done = await settled(manager, run_id, 'needs_attention')
        assert 'read-only' in done['error']
    asyncio.run(run())


def test_decline_stops_the_plan(repo):
    async def run():
        async def runner(*args, **kwargs):
            return document('output')
        _, manager = managers(repo, runner)
        run_id = (await manager.start_plan(THREE_STEPS, approved=True))['run_id']
        await settled(manager, run_id, 'awaiting_handoff')
        done = manager.decline(run_id)
        assert done['state'] == 'declined'
        assert done['transfers'][0]['state'] == 'declined'
        assert all(s['state'] in ('completed', 'pending') for s in done['steps'])
    asyncio.run(run())


def test_requires_approval_and_validates_plan(repo):
    async def run():
        _, manager = managers(repo, lambda *a, **k: None)
        with pytest.raises(ValueError, match='approval'):
            await manager.start_plan(THREE_STEPS, approved=False)
        with pytest.raises(ValueError, match='at least two'):
            await manager.start_plan([THREE_STEPS[0]], approved=True)
        with pytest.raises(ValueError, match='unknown participant'):
            manager.preflight_plan([
                step_spec('r', 'ghost', 'task', 'advice'),
                step_spec('i', 'b', 'task', 'patch'),
            ])
    asyncio.run(run())


def test_preflight_plan_binds_full_plan(repo):
    _, manager = managers(repo, lambda *a, **k: None)
    a = manager.preflight_plan(THREE_STEPS)
    changed = [dict(s) for s in THREE_STEPS]
    changed[1] = step_spec('implementation', 'b', 'different task', 'patch')
    b = manager.preflight_plan(changed)
    assert a['plan_sha256'] != b['plan_sha256']
    assert a['flow'] == 'plan' and len(a['steps']) == 3


def test_policy_requires_high_approval_for_plan_tools(repo):
    from odessy.authority.policy import collaboration_decision
    from odessy.runtime.types import ToolRequest

    participants = {
        'a': profile(repo, 'a'), 'b': profile(repo, 'b'), 'c': profile(repo, 'c'),
    }
    plan = collaboration_decision(ToolRequest('agent.plan_run', {'steps': THREE_STEPS}), participants)
    assert plan.action == 'confirm' and plan.risk == 'high'
    cont = collaboration_decision(ToolRequest('agent.plan_continue', {}), participants)
    assert cont.action == 'confirm' and cont.risk == 'high'
    # an unconfigured participant is denied
    bad = [step_spec('r', 'ghost', 'task', 'advice'), step_spec('i', 'b', 'task', 'patch')]
    denied = collaboration_decision(ToolRequest('agent.plan_run', {'steps': bad}), participants)
    assert denied.action == 'deny'
    # too few steps denied
    denied2 = collaboration_decision(ToolRequest('agent.plan_run', {'steps': [THREE_STEPS[0]]}), participants)
    assert denied2.action == 'deny'


def test_plan_tools_registered_and_grant_is_single_use():
    from odessy.collaboration.context import ApprovalGrants
    from odessy.collaboration.tools import build_collaboration_server

    async def list_names():
        server = build_collaboration_server(None, plan_runs=object())
        return [tool.name for tool in await server.list_tools()]

    names = asyncio.run(list_names())
    assert {'agent.plan_run', 'agent.plan_continue', 'agent.plan_status', 'agent.plan_cancel'} <= set(names)

    # The start/handover tools redeem a one-use, tool-bound grant.
    grants = ApprovalGrants()
    token = grants.issue('agent.plan_run')
    assert grants.redeem(token, 'agent.plan_run') is True
    assert grants.redeem(token, 'agent.plan_run') is False  # one use
    other = grants.issue('agent.plan_continue')
    assert grants.redeem(other, 'agent.plan_run') is False  # bound to its tool
