"""Local checkout desktop: real API, isolated workspace, no model downloads."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--port', required=True, type=int)
    args = parser.parse_args()
    root = Path(args.root).resolve(strict=True)
    state = root / 'build' / 'local-desktop' / 'state'
    workspace = root / 'build' / 'local-desktop' / 'workspace'
    state.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    # Keep desktop configuration and trust separate from the user's installation.
    os.environ['ODESSY_CONFIG_FILE'] = str(state / 'odessy.env')
    os.environ['ODESSY_COLLABORATION_FILE'] = str(state / 'collaboration.toml')
    os.environ['ODESSY_LINK_STATE_DIR'] = str(state / 'link')
    os.environ['ODESSY_MONITOR_STATE_DIR'] = str(state / 'monitor')
    os.environ['ODESSY_MONITORING'] = 'off'
    # No model is selected on the user's behalf. A harmless local placeholder
    # allows the real settings service to open until a profile is configured.
    os.environ['ODESSY_MODEL_NAME'] = 'not-configured'
    os.environ['ODESSY_MODEL_ENDPOINT'] = 'http://127.0.0.1:11434'
    monitor = root / 'build' / 'local-desktop' / 'bin' / 'odessy-monitor.exe'
    link = root / 'build' / 'odessy-link' / 'odessy-link.exe'
    if monitor.is_file() and link.is_file():
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        identity = state / 'link' / 'identity'
        initialized = (identity / 'identity.dpapi').is_file() or (identity / 'identity.json').is_file()
        if not initialized:
            result = subprocess.run([str(link), 'init'], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=15, creationflags=flags)
            initialized = result.returncode == 0
        if initialized:
            os.environ.update(ODESSY_MONITORING='on', ODESSY_MONITOR_LOCAL='on',
                              ODESSY_MONITOR_BIN=str(monitor), ODESSY_LINK_BIN=str(link))
    from odessy.application.serve import run_server
    asyncio.run(run_server(port=args.port, workspace=workspace, backend='lfm', watch_stdin=True,
                           token=os.environ['ODESSY_API_TOKEN'], print_token=False))


if __name__ == '__main__':
    main()
