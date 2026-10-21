"""Loopback HTTP server composition; optional dependencies stay lazy."""
from __future__ import annotations

import asyncio
import ipaddress
import os
from pathlib import Path
import sys


def validate_binding(host, port):
    if host != 'localhost':
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError('The HTTP server requires a loopback host (127.0.0.1, ::1, or localhost)')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('The HTTP server port must be between 1 and 65535')
    return host, port


async def run_server(*, host='127.0.0.1', port=8765, use_fake_model=False,
                     backend=None, token=None, workspace=None):
    host, port = validate_binding(host, port)
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError('Install odessy[ui] to run the HTTP server') from exc
    from odessy.application.api import create_app
    from odessy.application.bootstrap import create_controller
    from odessy.collaboration.config import load_config

    workspace = Path(workspace or Path.cwd()).resolve()
    turn_lock = asyncio.Lock()

    async def factory():
        # Each session captures a freshly validated config and owns its resources.
        return await create_controller(workspace=workspace, use_fake_model=use_fake_model,
                                       backend=backend, turn_lock=turn_lock)

    def profiles():
        config = load_config()
        return {'current': {'name': 'current', 'model': 'fake' if use_fake_model else backend or 'lfm'},
                'auto': {'name': 'auto', 'adapter': 'auto'},
                'local': {'name': 'local', 'adapter': 'lfm'},
                **{name: profile.public_dict() for name, profile in config.participants.items()}}

    app = create_app(factory, config=load_config(), profiles=profiles,
                     token=token if token is not None else os.environ.get('ODESSY_API_TOKEN') or None)
    address = f'[{host}]' if ':' in host else host
    print(f'[odessy] HTTP API: http://{address}:{port}', file=sys.stderr, flush=True)
    print(f'[odessy] Bearer token: {app.state.auth_token}', file=sys.stderr, flush=True)
    print('[odessy] Open / for diagnostics; send Authorization: Bearer <token> on every /v1 request.',
          file=sys.stderr, flush=True)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, access_log=False, log_level='warning'))
    await server.serve()

