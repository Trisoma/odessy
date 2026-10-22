"""Access checks for private runtimes, without elevation or automatic ACL changes."""
from __future__ import annotations
import os
import stat
from pathlib import Path


def check_runtime_access(runtime: Path):
    for relative in ('python-runtime/python.exe', 'node-runtime/node.exe'):
        path = runtime / relative
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode): raise ValueError('Runtime file is not a regular file: ' + str(path))
            with path.open('rb') as stream: stream.read(1)
        except PermissionError as error:
            raise ValueError('The current account cannot read or execute the old runtime: ' + str(path) +
                             ". Fix this installation's account permissions; the program will not elevate "
                             'privileges automatically and will not start the model service.') from error
        except FileNotFoundError as error:
            raise ValueError('Runtime file is missing: ' + str(path) + ' Check that the installation is complete; a missing file and insufficient permissions are different problems.') from error


def is_elevated():
    if os.name != 'nt': return False
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin())
