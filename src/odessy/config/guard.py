"""Noticing a change to the files that decide what is asked.

``hooks.toml`` has its own fingerprint (``odessy.runtime.hooks``), because its scripts
are watched with it. ``sandbox.toml`` and ``commands.toml`` get the same treatment here:
the fingerprint of each is taken the first time this process looks, and a file that
changes, appears or disappears afterwards denies every call until Odessy restarts.
A running agent must not be able to widen its own limits, by any route.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from pathlib import Path

_BASELINES: dict[str, str] = {}


def _fingerprint(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"


def _key(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().absolute()))


class ConfigGuard:
    """The config files one agent depends on."""

    def __init__(self, paths: Iterable[Path]) -> None:
        self.paths = tuple(Path(path) for path in paths)
        for path in self.paths:
            _BASELINES.setdefault(_key(path), _fingerprint(path))

    def changed(self) -> str | None:
        """Why this agent can no longer be trusted, or None."""

        for path in self.paths:
            if _fingerprint(path) != _BASELINES[_key(path)]:
                return f"{path.name} changed after Odessy read it; restart Odessy to load it again"
        return None


def reset_guard() -> None:
    """Forget the fingerprints (tests; a restart does the same)."""

    _BASELINES.clear()


__all__ = ["ConfigGuard", "reset_guard"]
