"""Odessy v0.2.0 (under development) command-line agent runtime.

``__version__`` is the single source of truth for the runtime version. Anything
that needs to stamp a version -- benchmark metadata in particular -- must read it
from here rather than repeat the literal, which is how the version previously
drifted out of step with ``pyproject.toml`` and the README.

The ``.dev0`` suffix is deliberate: v0.2.0 is under development and not released.
Bump to ``0.2.0`` only when the release gate is actually passed.
"""

__version__ = "0.2.0.dev0"

__all__ = [
    "agent_loop",
    "command_bridge",
    "fake_model",
    "lfm_model",
    "mcp_server",
    "ollama_model",
    "policy",
    "types",
    "voice",
]
