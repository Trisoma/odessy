"""Resource limits for one confined command: memory, processes, file size, CPU time.

The authority layer sends them with the call (``limits``), from ``sandbox.toml``;
the executor applies what its platform can and reports the rest:

- POSIX: ``setrlimit`` in the child before it runs (``RLIMIT_DATA``,
  ``RLIMIT_FSIZE``, ``RLIMIT_CPU``). They are inherited through bwrap and by every
  process the command starts. The process count is not applied: ``RLIMIT_NPROC``
  counts every process of the user, not of the command, so a limit low enough to
  stop a fork bomb would stop an ordinary desktop session from starting anything.
- Windows: a job object joined by a small host before the command starts
  (``_limit_host.py``): per-process memory, active processes and job CPU time. A
  file-size limit has no Windows equivalent.

A limit of ``None`` (``0`` in ``sandbox.toml``) is off.
"""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

__all__ = ["DEFAULT_LIMITS", "ResourceLimits", "exceeded_limit", "limit_report", "posix_preexec", "windows_limit_argv"]

#: Ceilings a call may ask for; anything above is clamped, so a forged call cannot
#: ask for "unlimited" by sending a huge number.
_CEILINGS = {"memory_mb": 1 << 20, "processes": 4096, "file_size_mb": 1 << 20, "cpu_seconds": 3600}


@dataclass(frozen=True)
class ResourceLimits:
    memory_mb: int | None = 4096
    processes: int | None = 256
    file_size_mb: int | None = 4096
    cpu_seconds: int | None = 120

    def to_arguments(self) -> dict[str, int]:
        return {key: value for key, value in asdict(self).items() if value}

    @classmethod
    def from_arguments(cls, value: Mapping[str, Any] | None) -> "ResourceLimits | None":
        """Limits from a call, validated and clamped; None when the call sent none."""

        if not value:
            return None
        fields: dict[str, int | None] = {}
        for key, ceiling in _CEILINGS.items():
            raw = value.get(key)
            if raw is None or isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
                fields[key] = None
            else:
                fields[key] = min(raw, ceiling)
        return cls(**fields)


DEFAULT_LIMITS = ResourceLimits()


def posix_preexec(limits: ResourceLimits | None) -> Callable[[], None] | None:
    """A ``preexec_fn`` that applies the limits in the child, or None."""

    if limits is None or os.name == "nt":
        return None
    import resource

    wanted: list[tuple[int, int]] = []
    if limits.memory_mb:
        wanted.append((resource.RLIMIT_DATA, limits.memory_mb * 1024 * 1024))
    if limits.file_size_mb:
        wanted.append((resource.RLIMIT_FSIZE, limits.file_size_mb * 1024 * 1024))
    if limits.cpu_seconds:
        wanted.append((resource.RLIMIT_CPU, limits.cpu_seconds))
    if not wanted:
        return None

    def apply() -> None:
        for kind, value in wanted:
            _, hard = resource.getrlimit(kind)
            value = value if hard == resource.RLIM_INFINITY else min(value, hard)
            try:
                resource.setrlimit(kind, (value, value if hard == resource.RLIM_INFINITY else hard))
            except (ValueError, OSError):
                pass

    return apply


def windows_limit_argv(limits: ResourceLimits | None, argv: list[str]) -> list[str]:
    """``argv`` run inside a limited job (Windows), or unchanged."""

    if limits is None or os.name != "nt" or not (limits.memory_mb or limits.processes or limits.cpu_seconds):
        return argv
    host = Path(__file__).with_name("_limit_host.py")
    return [
        sys.executable, "-I", str(host),
        str(limits.memory_mb or 0), str(limits.processes or 0), str(limits.cpu_seconds or 0),
        "--", *argv,
    ]


def limit_report(limits: ResourceLimits | None) -> dict[str, Any]:
    """Which limits this platform applied, for the result's ``sandbox`` field."""

    if limits is None:
        return {"applied": {}, "not_applied": []}
    requested = limits.to_arguments()
    unsupported = {"processes"} if os.name != "nt" else {"file_size_mb"}
    return {
        "applied": {key: value for key, value in requested.items() if key not in unsupported},
        "not_applied": sorted(key for key in requested if key in unsupported),
    }


def exceeded_limit(returncode: int | None) -> str | None:
    """The limit a POSIX exit signal points to (CPU time or file size), if any.

    The command runs under a shell, which reports a child killed by a signal as
    ``128 + signal``; the shell itself killed shows as ``-signal``.
    """

    if returncode is None or returncode == 0 or os.name == "nt":
        return None
    signum = -returncode if returncode < 0 else returncode - 128
    if signum == getattr(signal, "SIGXCPU", -1):
        return "cpu_seconds"
    if signum == getattr(signal, "SIGXFSZ", -1):
        return "file_size_mb"
    return None
