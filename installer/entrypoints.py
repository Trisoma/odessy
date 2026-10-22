"""Commands for source and frozen entry points, including independent EXE sessions."""
from __future__ import annotations
import os
import sys

MODES = {'installer.wizard': 'wizard', 'installer.launcher': 'launcher', 'installer.start': 'start', 'installer.uninstall_ui': 'uninstall', 'installer.maintenance_ui': 'maintenance'}


def entry_command(module, arguments):
    if module not in MODES: raise ValueError('Unsupported installer entry point.')
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--mode', MODES[module], *map(str, arguments)]
    return [sys.executable, '-m', module, *map(str, arguments)]


def independent_environment():
    env = dict(os.environ)
    if getattr(sys, 'frozen', False):
        # An independent onefile instance must own its own extraction directory.
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env
