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
                     backend=None, token=None, workspace=None, watch_stdin=False,
                     print_token=True, runtime_configuration=None, desktop_voice=None):
    host, port = validate_binding(host, port)
    token = token if token is not None else os.environ.get('ODESSY_API_TOKEN') or None
    if print_token and not token:
        raise ValueError('Set ODESSY_API_TOKEN before starting the manual HTTP server; credentials are never printed to logs.')
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
        from odessy.models.cloud_model import CLOUD_PROVIDERS
        chosen = backend or os.environ.get('ODESSY_MODEL_BACKEND', 'lfm')
        model = (os.environ.get('ODESSY_CLOUD_MODEL') or CLOUD_PROVIDERS[chosen].default_model) if chosen in CLOUD_PROVIDERS else os.environ.get('ODESSY_MODEL_NAME') or os.environ.get('ODESSY_OLLAMA_MODEL') or chosen
        return {'current': {'name': 'current', 'provider': chosen, 'model': 'fake' if use_fake_model else model},
                'auto': {'name': 'auto', 'adapter': 'auto'},
                'local': {'name': 'local', 'adapter': 'lfm'},
                **{name: profile.public_dict() for name, profile in config.participants.items()}}

    app = create_app(factory, config=load_config(), profiles=profiles, runtime_configuration=runtime_configuration, desktop_voice=desktop_voice,
                     token=token)
    address = f'[{host}]' if ':' in host else host
    print(f'[odessy] HTTP API: http://{address}:{port}', file=sys.stderr, flush=True)
    if print_token:
        print('[odessy] Authentication uses the configured API token; its value is not logged.', file=sys.stderr, flush=True)
    print('[odessy] Open / for diagnostics; send Authorization: Bearer <token> on every /v1 request.',
          file=sys.stderr, flush=True)
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, access_log=False, log_level='warning'))

    if watch_stdin:
        # The owning desktop keeps this pipe open; EOF means it is gone, so the
        # coordinator must not outlive the application that started it.
        import threading

        def watch():
            try:
                while raw := sys.stdin.buffer.readline(65537):
                    # The owned desktop may rotate session-only credentials.
                    # This private channel is not an HTTP/pairing operation.
                    from odessy.credentials import acknowledge_startup
                    import io
                    acknowledge_startup(io.BytesIO(raw), sys.stdout)
            except (OSError, ValueError, RuntimeError):
                pass
            server.should_exit = True

        threading.Thread(target=watch, name='odessy-parent-watch', daemon=True).start()

    try:
        await server.serve()
    finally:
        from odessy.tools.lan_executor import clear
        clear()
