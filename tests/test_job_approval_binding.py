import asyncio
from dataclasses import replace

import pytest

from odessy.application.session import SessionController
from odessy.collaboration.config import ParticipantProfile
from odessy.collaboration.jobs import JobManager
from odessy.runtime.types import ToolRequest


def test_job_preflight_is_pure_and_preserves_foreign_paths(tmp_path):
    profile = ParticipantProfile('coder', 'codex', capabilities=('delegate',), workspace=str(tmp_path))
    jobs = JobManager({'coder': profile})
    target = jobs.preflight_submission('coder', 'review')
    assert target['host'] == 'local'
    assert target['workspace'] == str(tmp_path.resolve())
    assert not jobs._jobs
    with pytest.raises(ValueError, match='workspace'):
        jobs.preflight_submission('coder', 'review', workspace=str(tmp_path.parent))
    jobs.profiles['coder'] = replace(profile, host='laptop', workspace=r'C:\work')
    with pytest.raises(ValueError, match='unavailable'):
        jobs.preflight_submission('coder', 'review')
    jobs.remote_targets['laptop'] = lambda *args: None
    assert jobs.preflight_submission('coder', 'review')['workspace'] == r'C:\work'


def test_direct_job_approval_rejects_changed_profile_and_preflights_before_prompt(tmp_path):
    async def run():
        profile = ParticipantProfile('coder', 'codex', capabilities=('delegate',), workspace=str(tmp_path))
        jobs = JobManager({'coder': profile})
        controller = SessionController(model_factory=lambda name: None, agent_factory=lambda *a, **kw: None,
            profiles={'local': {}}, jobs=jobs)
        with pytest.raises(ValueError, match='unknown'):
            await asyncio.wait_for(controller.confirm({'risk': 'high'}, ToolRequest('agent.delegate', {'participant': 'missing', 'task': 'review'})), .1)
        assert not controller.pending_approvals
        pending = asyncio.create_task(controller.confirm({'risk': 'high'}, ToolRequest('agent.delegate', {'participant': 'coder', 'task': 'review'})))
        await asyncio.sleep(0)
        approval = controller.pending_approvals[0]
        assert approval['operation'] == 'agent.delegate'
        assert approval['target_identity']['profile']['adapter'] == 'codex'
        jobs.profiles['coder'] = replace(profile, command=('other-cli',))
        controller.answer_approval(approval['id'], True)
        with pytest.raises(PermissionError, match='changed'):
            await pending
        await controller.close()
    asyncio.run(run())


def test_jobs_api_rejects_unavailable_target_before_creating_approval(tmp_path):
    async def run():
        import httpx
        from odessy.application.api import create_app
        profile = ParticipantProfile('coder', 'codex', host='laptop', capabilities=('delegate',), workspace=r'C:\work')
        jobs = JobManager({'coder': profile})
        controller = SessionController(model_factory=lambda name: None, agent_factory=lambda *a, **kw: None,
            profiles={'local': {}}, jobs=jobs)
        app = create_app(lambda: controller, token='test-secret')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test',
                headers={'Authorization': 'Bearer test-secret'}) as client:
            sid = (await client.post('/v1/sessions')).json()['id']
            response = await asyncio.wait_for(client.post('/v1/sessions/' + sid + '/jobs',
                json={'participant': 'coder', 'task': 'review'}), 1)
            assert response.status_code == 400
            assert 'unavailable' in response.json()['detail']
            assert not controller.pending_approvals
        await controller.close()
    asyncio.run(run())
