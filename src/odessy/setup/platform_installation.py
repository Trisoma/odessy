"""The desktop-integration implementation for the running platform.

Windows registers a Start-menu shortcut, an Installed-apps record and a maintenance
cache; Linux registers an owned launcher and an application-menu entry. The rest of
the installer talks to one contract, so no caller branches on the platform: it asks
this module for ``launch``, ``register``, ``refresh``, ``unregister`` and
``helper_cleanup``.
"""
from __future__ import annotations
import os
import sys

# The installed desktop binary is platform-specific; the installer record and the
# ``installed`` specification both have to use the same relative path.
DESKTOP_RELATIVE='desktop/odessy_desktop.exe' if os.name=='nt' else 'desktop/odessy_desktop'


def implementation():
    """Return the platform module, or None when this platform has no adapter yet."""
    if sys.platform=='win32':
        from . import windows_installation as module
        return module
    if sys.platform.startswith('linux'):
        from . import linux_installation as module
        return module
    return None


def _require(operation):
    module=implementation()
    if module is None:
        raise ValueError(f'{operation} is not implemented for this platform yet')
    return module


def display_version():
    module=implementation()
    return getattr(module,'DISPLAY_VERSION','0.5.0.dev0') if module is not None else '0.5.0.dev0'


def launch(root,mode='app',launch_id=None):
    """Start the recorded desktop for a mode and return the child process."""
    return _require('launching the desktop').launch(root,mode,launch_id=launch_id)


def register(root,record,payload=None):
    """Create the platform's user-facing entries and return the recorded resources."""
    return _require('desktop registration').register(root,record,payload=payload)


def refresh(root,record):
    """Keep the registered entries pointing at the version that is actually active."""
    return _require('desktop registration').refresh(root,record)


def unregister(root,record):
    """Remove the recorded entries that are still unchanged."""
    return _require('desktop registration').unregister(root,record)


def helper_cleanup(record):
    """Remove only recorded, unchanged maintenance-cache files."""
    return _require('maintenance cleanup').helper_cleanup(record)
