"""Odessy v0.2.0 (under development) command-line agent runtime.

``__version__`` is the single source of truth for the runtime version. Anything
that needs to stamp a version -- benchmark metadata in particular -- must read it
from here rather than repeat the literal, which is how the version previously
drifted out of step with ``pyproject.toml`` and the README.

The ``.dev0`` suffix is dropped because the v0.2.0 release gate was passed on
2026-09-24. Reinstate it (``0.3.0.dev0``) for the next development cycle.
"""

__version__ = "0.2.0"

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
