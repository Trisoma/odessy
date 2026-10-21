import asyncio
import json

import pytest
from mcp import Client
from mcp.server import MCPServer

from odessy.collaboration.config import CollaborationConfig, CollaborationSettings, ParticipantProfile


def test_worker_requires_trusted_stdio_and_enabled_configuration(monkeypatch):
    from odessy.collaboration import worker
    with pytest.raises(ValueError, match='trusted stdio'):
        worker.run_worker()
    monkeypatch.setattr(worker, 'load_config', lambda path=None: CollaborationConfig())
    with pytest.raises(ValueError, match='enabled'):
        worker.run_worker(trusted_stdio_operator=True)


def test_worker_stdio_admits_only_configured_delegation_and_closes(monkeypatch, tmp_path, capsys):
    from odessy.collaboration import worker
    from odessy.collaboration.jobs import JobManager
    config = CollaborationConfig(CollaborationSettings(enabled=True), {
        'coder': ParticipantProfile('coder', 'codex', workspace=str(tmp_path), capabilities=('consult', 'delegate'), allowed_modes=('advice',)),
        'adviser': ParticipantProfile('adviser', 'codex', workspace=str(tmp_path), capabilities=('consult',)),
    })
    closed, seen = [], []
    async def runner(command, **kwargs):
        seen.append(command)
        return {'returncode': 0, 'output': '{"type":"result","result":"safe opinion"}'}
    class OfflineJobs(JobManager):
        def __init__(self, profiles):
            super().__init__(profiles, runner=runner)
        async def close(self):
            await super().close()
            closed.append(True)
    monkeypatch.setattr(worker, 'load_config', lambda path=None: config)
    monkeypatch.setattr(worker, 'JobManager', OfflineJobs)
    async def stdio(server):
        async with Client(server) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert names == {'model.consult', 'agent.delegate', 'agent.job_status', 'agent.cancel'}
            denied = await client.call_tool('agent.delegate', {'participant': 'adviser', 'task': 'do work'})
            assert denied.is_error
            denied = await client.call_tool('agent.delegate', {'participant': 'coder', 'task': 'do work', 'mode': 'patch'})
            assert denied.is_error
            answer = await client.call_tool('model.consult', {'participant': 'adviser', 'question': 'review?'})
            answer_data = answer.structured_content or json.loads(answer.content[0].text)
            assert answer_data['answer'] == 'safe opinion'
            job = await client.call_tool('agent.delegate', {'participant': 'coder', 'task': 'review'})
            job_data = job.structured_content or json.loads(job.content[0].text)
            assert job_data['job_id']
    monkeypatch.setattr(MCPServer, 'run_stdio_async', stdio)
    worker.run_worker(trusted_stdio_operator=True)
    assert closed and seen and 'read-only' in seen[0]
    assert 'child' in capsys.readouterr().err.lower()
