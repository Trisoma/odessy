"""Opt-in loopback fixture: real API/broker, fake agent, no tools or providers.

Run with PYTHONPATH=src and an interpreter with odessy[ui] dependencies.
All persistence goes to a temporary directory. Never use as a production service.
"""
import asyncio
from pathlib import Path
import tempfile

import uvicorn

from odessy.application.api import create_app
from odessy.application.session import SessionController
from odessy.collaboration.config import CollaborationConfig
from odessy.interaction.context import current_interactions
from odessy.runtime.types import AgentTurnResult


class FixtureAgent:
    def __init__(self, model, **kwargs):
        self.confirm = kwargs['confirm']

    async def run_turn(self, text, **kwargs):
        if text == '/approval':
            approved = await self.confirm({'reason': 'Fixture authorization'},
                {'name': 'terminal.run', 'arguments': {'command': 'echo fixture'}})
            return AgentTurnResult('Approved' if approved else 'Denied', [])
        if text == '/question':
            broker = current_interactions()
            result = await broker.ask(question='Which repository?',
                choices=['Odessy', 'Other'], allow_free_text=True)
            return AgentTurnResult(str(result), [])
        if text == '/slow':
            await asyncio.sleep(30)
        return AgentTurnResult('Fixture reply: ' + text, [])


def main():
    with tempfile.TemporaryDirectory(prefix='odessy-ui-fixture-') as temp:
        app = create_app(lambda: SessionController(model_factory=lambda name: name,
            agent_factory=FixtureAgent, profiles={'local': {}, 'auto': {}}),
            token='desktop-fixture-only', config=CollaborationConfig(),
            config_path=Path(temp) / 'collaboration.toml', profiles={'local': {}, 'auto': {}})
        uvicorn.run(app, host='127.0.0.1', port=8876, access_log=False, log_level='warning')


if __name__ == '__main__':
    main()
