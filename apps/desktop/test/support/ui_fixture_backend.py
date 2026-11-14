"""Owned loopback fixture: real API/session/policy/broker, deterministic provider.

Only a TemporaryDirectory can be written. No shell, microphone or remote access.
This is test support and must never be included in the application wheel.
"""
import asyncio
import json
import os
from pathlib import Path
import site
import socket
import sys
import tempfile
from types import SimpleNamespace

if os.environ.get('ODESSY_UI_TEST_SITE'):
    site.addsitedir(os.environ['ODESSY_UI_TEST_SITE'])
repo = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(repo / 'src'))
from odessy.application.api import create_app
from odessy.application.session import SessionController
from odessy.application.workspace_inspector import bind_write_review
from odessy.authority.action_approval import build_action_approval
from odessy.authority.policy import OdessyPolicy
from odessy.runtime.types import AgentTurnResult, ToolObservation, ToolRequest
import uvicorn

with tempfile.TemporaryDirectory(prefix='odessy-ui-service-') as temp:
    root = Path(temp)
    (root / 'readme.txt').write_bytes(b'Before fixture\n')
    (root / '.env').write_text('fixture-sensitive', encoding='utf-8')
    os.environ['ODESSY_HOOKS_FILE'] = str(root / 'no-hooks.toml')
    os.environ.pop('ODESSY_HOOKS_REQUIRED', None)
    os.environ['ODESSY_MONITOR_ENABLED'] = '0'
    authority = SimpleNamespace(allowed=True)

    class Agent:
        def __init__(self, owner):
            self.owner = owner

        async def run_turn(self, text, **kwargs):
            owner = self.owner
            if text == 'wait':
                await asyncio.sleep(60)
            if text == 'revoke fixture access':
                authority.allowed = False
                return AgentTurnResult('Fixture workspace access revoked', [])
            if text == 'propose fixture change':
                request = ToolRequest('filesystem.write', {'path': 'readme.txt', 'content': 'After fixture\n'})
                policy = OdessyPolicy(root)
                decision = policy.evaluate(request)
                plan = build_action_approval(request, decision, policy, call_id='fixture-write', execution_target='local')
                plan = await bind_write_review(plan, request, owner._workspace_inspector, owner.id)
                approved = await owner.confirm_action(plan.public_dict())
                if not approved:
                    return AgentTurnResult('Fixture change denied', [])
                if not plan.matches(request, decision, policy, execution_target='local'):
                    raise PermissionError('Fixture change became stale')
                (root / 'readme.txt').write_bytes(request.arguments['content'].encode('utf-8'))
                return AgentTurnResult('Fixture change applied after exact approval', [])
            return AgentTurnResult('Offline response: ' + text, [], tool_observations=[
                ToolObservation(ToolRequest('terminal.run'), {'status': 'ok', 'stdout': 'Provider fixture output; no command executed.', 'stderr': '', 'exit_code': 0}, 'fixture-output')])

    async def factory():
        owner = SessionController(model_factory=lambda name: name,
            agent_factory=lambda model, **kwargs: Agent(owner), profiles={'local': {}}, runtime_selection='legacy')
        closing = asyncio.Event()
        owner.resources = SimpleNamespace(closing=closing, task=asyncio.create_task(closing.wait()))
        owner._workspace_root = root
        owner._workspace_policy_factory = lambda: OdessyPolicy(root)
        owner._workspace_authorized = lambda: authority.allowed
        close = owner.close
        async def close_owned():
            await close()
            closing.set()
            await owner.resources.task
        owner.close = close_owned
        return owner

    app = create_app(factory, token='offline-fixture-only', runtime_configuration=None)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    print(json.dumps({'port': port}), flush=True)
    config = uvicorn.Config(app, log_level='error', access_log=False)
    server = uvicorn.Server(config)
    @app.post('/__fixture/shutdown')
    async def shutdown_fixture():
        server.should_exit = True
        return {'status': 'closing'}
    asyncio.run(server.serve(sockets=[sock]))
