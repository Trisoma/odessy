"""Offline loopback test fixture: real application, explicit fake model only.

Never an application fallback. Owns a temporary profile and ephemeral port.
"""
import asyncio
from pathlib import Path
import socket
import tempfile
import subprocess
from types import SimpleNamespace

import uvicorn

from odessy.application.api import create_app
from odessy.application.session import SessionController
from odessy.runtime.types import AgentTurnResult


class FixtureAgent:
    def __init__(self, *args, **kwargs):
        pass

    async def run_turn(self, text, **kwargs):
        return AgentTurnResult('Connected preflight reply: ' + text, [])


def main():
    with tempfile.TemporaryDirectory(prefix='odessy-basics-preflight-') as profile:
        workspace = Path(profile) / 'workspace'
        workspace.mkdir()
        (workspace / 'boat.md').write_text('# Aurora\nAurora is the blue Odessy boat.\n', encoding='utf-8')
        subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
        subprocess.run(['git', '-C', str(workspace), 'add', 'boat.md'], check=True)
        subprocess.run(['git', '-C', str(workspace), '-c', 'user.name=Fixture',
                        '-c', 'user.email=fixture@example.test', 'commit', '-qm', 'fixture'], check=True)
        def factory():
            from odessy.authority.policy import OdessyPolicy
            controller = SessionController(model_factory=lambda _: object(),
                agent_factory=FixtureAgent, profiles={'current': {}})
            controller.resources = SimpleNamespace(
                task=asyncio.create_task(asyncio.Event().wait()), closing=asyncio.Event())
            controller._workspace_root = workspace
            controller._workspace_policy_factory = lambda: OdessyPolicy(workspace)
            controller._workspace_authorized = lambda: True
            return controller
        app = create_app(factory,
            token='preflight-fixture-only', config_path=Path(profile) / 'profile.env',
            conversations_enabled=True, profiles={'current': {}})
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            listener.listen(64)
            server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                log_level='error', access_log=False))
            async def run():
                task = asyncio.create_task(server.serve(sockets=[listener]))
                while not server.started and not task.done():
                    await asyncio.sleep(.01)
                if not server.started:
                    await task
                    raise RuntimeError('Fixture server did not start')
                print(f'PREFLIGHT_URL=http://127.0.0.1:{port}', flush=True)
                # Stdin EOF is a graceful, ownership-scoped shutdown, not a kill-by-name.
                await asyncio.to_thread(input)
                server.should_exit = True
                await task
            asyncio.run(run())


if __name__ == '__main__':
    main()
