"""Explicitly launched, trusted-operator stdio MCP coding worker.

CLI integration calls ``run_worker(trusted_stdio_operator=True)`` only for its
dedicated worker command. There is no HTTP listener or ambient launch path.
The stdio caller is an admitted execution operator and approves jobs; individual
profiles still decide capabilities, modes, workspaces, and child CLI settings.
"""
from __future__ import annotations

import asyncio
import sys

from odessy.collaboration.config import load_config
from odessy.collaboration.jobs import JobManager
from odessy.collaboration.participants import CollaborationService
from odessy.collaboration.tools import build_collaboration_server
from odessy.models.usage import process_ledger


async def _serve(config):
    jobs = JobManager(config.participants)
    loop = asyncio.get_running_loop()

    def coding_advice(profile, question, context):
        future = asyncio.run_coroutine_threadsafe(jobs.consult(profile, question, context), loop)
        try:
            return future.result(timeout=profile.timeout + 10)
        except BaseException:
            future.cancel()
            raise

    class OperatorConsultations:
        def consult(self, participant='', question='', context='', reason=''):
            # Each incoming request is one explicit consultation turn. Workers
            # never forward to another host, so stdio advice cannot recurse.
            name = participant or config.settings.default_participant
            profile = config.participants.get(name)
            if profile is None or profile.host != 'local':
                return {'status': 'error', 'participant': name, 'answer': '',
                        'reason': 'Worker advice requires a configured local participant'}
            service = CollaborationService(config, adapters={
                adapter: coding_advice for adapter in ('codex', 'claude_code', 'custom_agent')
            })
            service.start_turn(question)
            return service.consult(participant=name, question=question, context=context,
                                   reason=reason, explicit=True)

    try:
        server = build_collaboration_server(OperatorConsultations(), jobs)
        if not any('consult' in p.capabilities for p in config.participants.values()):
            server.remove_tool('model.consult')
        if not any('delegate' in p.capabilities for p in config.participants.values()):
            for name in ('agent.delegate', 'agent.job_status', 'agent.cancel'):
                server.remove_tool(name)
        print('[odessy] trusted stdio coding worker: configured capabilities and modes apply; '
              'child CLIs retain their own permissions and authentication; CLI usage is outside the API budget.',
              file=sys.stderr, flush=True)
        await server.run_stdio_async()
    finally:
        await jobs.close()


def run_worker(config_path=None, *, trusted_stdio_operator=False):
    """Serve stdio until EOF, then cancel outstanding children and close jobs."""
    if trusted_stdio_operator is not True:
        raise ValueError('worker requires an explicit trusted stdio operator launch')
    config = load_config(config_path)
    if not config.settings.enabled:
        raise ValueError('collaboration must be enabled before starting the coding worker')
    process_ledger(allow_profile_prices=any(
        profile.adapter == 'cloud' and profile.pricing is not None
        for profile in config.participants.values()
    ))
    asyncio.run(_serve(config))
