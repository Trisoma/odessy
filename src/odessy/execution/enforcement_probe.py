"""Measure what this host's confinement backend actually enforces.

Why a separate module from the translation: a deterministic unit test can prove that the
policy was *translated* correctly, and nothing else. Whether a backend denies a write, hides
a path or cuts the network is a fact about the operating system, and the only honest way to
learn it is to run a real command through the same ``confine()`` the executor uses and read
what happened.

The probes are deliberately small and side-effect free outside the two directories the
caller hands over:

``write_inside``
    A real write into the workspace. If this fails, the sandbox is broken rather than
    strict, and every other verdict is meaningless — it is the control.

``write_outside``
    A real write into a directory outside the workspace. ``enforced`` here means the write
    failed.

``read_hidden``
    A real read of a file in a directory the policy asks to hide. ``enforced`` means the read
    failed. On a host whose backend cannot mask paths this reads the file, which is exactly
    what "hidden paths are unenforced" looks like from the inside.

``network``
    A real TCP connect to a loopback listener the caller started. ``enforced`` means the
    connect failed because the backend cut the network — not because nothing was listening.

``symlink_escape``
    A write through a junction/symlink inside the workspace that points outside it.
    ``enforced`` means the write failed. A backend that matches on path prefixes rather than
    resolved targets lets it through, and that is worth knowing rather than assuming.

Every verdict carries its evidence: the exit code and the line the backend printed. A verdict
without evidence is not reported.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from odessy.execution.contract import ExecutionPolicy, ExecutionProvider

__all__ = ["EnforcementMeasurement", "Measurement", "probe_enforcement"]

#: Dimensions this module measures.
DIMENSIONS: tuple[str, ...] = (
    "write_inside",
    "write_outside",
    "read_hidden",
    "network",
    "symlink_escape",
)


@dataclass(frozen=True)
class Measurement:
    """One dimension, what happened, and the evidence for it."""

    dimension: str
    #: ``None`` when the probe could not run (the runner was absent, the junction could not
    #: be created). Never guess in that case: an unmeasured dimension is not enforced.
    enforced: bool | None
    detail: str
    exit_code: int | None = None
    stderr: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "enforced": self.enforced,
            "exit_code": self.exit_code,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class EnforcementMeasurement:
    """Every dimension measured through one provider under one policy."""

    provider_id: str
    enforcement: str
    mode: str
    workspace: str
    measurements: tuple[Measurement, ...] = field(default_factory=tuple)

    def verdict(self, dimension: str) -> bool | None:
        found = next((item for item in self.measurements if item.dimension == dimension), None)
        return None if found is None else found.enforced

    def enforced(self) -> tuple[str, ...]:
        return tuple(item.dimension for item in self.measurements if item.enforced is True)

    def measured(self) -> tuple[str, ...]:
        """Every dimension with a verdict, enforced or not (``None`` excluded)."""

        return tuple(item.dimension for item in self.measurements if item.enforced is not None)

    def unenforced(self) -> tuple[str, ...]:
        """Measured as *not* enforced. ``None`` (unmeasured) is listed separately."""

        return tuple(item.dimension for item in self.measurements if item.enforced is False)

    def unmeasured(self) -> tuple[str, ...]:
        return tuple(item.dimension for item in self.measurements if item.enforced is None)

    @property
    def usable(self) -> bool:
        """Whether the backend can run a legitimate command at all.

        The control probe writes inside the workspace the policy grants. A backend that
        refuses that is not strict, it is unusable: every "denied" verdict elsewhere would
        be indistinguishable from "nothing could run". A backend that cannot pass its own
        control cannot be counted as enforcement, whatever it declares.
        """

        return self.verdict("write_inside") is True

    @property
    def control_failure(self) -> Measurement | None:
        """The control measurement, when it did not pass."""

        if self.usable:
            return None
        return next((item for item in self.measurements if item.dimension == "write_inside"),
                    None)

    def contradicts(self, limitations: Iterable[str]) -> tuple[str, ...]:
        """Dimensions the plan calls *enforced* but which the probe measured as open.

        ``cannot_restrict_network`` means the backend cannot be asked to cut the network.
        If the probe measures the network as reachable while the declaration omits that
        limitation, the declaration is overstating the boundary — the one direction that
        must never be tolerated.
        """

        gaps = set(limitations)
        overstatements = []
        if "network" in self.unenforced() and "cannot_restrict_network" not in gaps:
            overstatements.append("network")
        if "read_hidden" in self.unenforced() and "cannot_hide_paths" not in gaps:
            overstatements.append("hidden_paths")
        if "write_outside" in self.unenforced() and self.enforcement == "full":
            overstatements.append("fs-write")
        return tuple(overstatements)

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "enforcement": self.enforcement,
            "mode": self.mode,
            "workspace": self.workspace,
            "measurements": [item.as_dict() for item in self.measurements],
        }


def _python() -> str:
    return sys.executable or "python"


def _run(argv: Sequence[str], *, cwd: str, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""


def _write_probe(path: Path) -> list[str]:
    return [_python(), "-c",
            "import sys; open(sys.argv[1], 'w', encoding='utf-8').write('probe'); "
            "print('WROTE')", str(path)]


def _read_probe(path: Path) -> list[str]:
    return [_python(), "-c",
            "import sys; print(open(sys.argv[1], encoding='utf-8').read()[:32])", str(path)]


def _connect_probe(port: int) -> list[str]:
    return [_python(), "-c",
            "import socket, sys; s = socket.create_connection(('127.0.0.1', int(sys.argv[1])), "
            "timeout=5); s.close(); print('CONNECTED')", str(port)]


def _junction(link: Path, target: Path) -> bool:
    """Create a directory junction (no privilege needed) or a symlink, whichever works."""

    if os.name == "nt":
        created = _run(["cmd", "/c", "mklink", "/J", str(link), str(target)], cwd=str(link.parent),
                       timeout=30)
        if created.returncode == 0 and link.exists():
            return True
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    return link.exists()


def _measure(dimension: str, rc: int, stdout: str, stderr: str, *, expect_failure: bool,
             subject: str) -> Measurement:
    if expect_failure:
        enforced = rc != 0
        detail = (
            f"{subject}: refused (exit {rc}) {_first_line(stderr) or _first_line(stdout)}"
            if enforced else
            f"{subject}: allowed (exit {rc}) {_first_line(stdout)} — the boundary is not there"
        )
    else:
        enforced = rc == 0
        detail = (
            f"{subject}: ran (exit {rc}) {_first_line(stdout)}"
            if enforced else
            f"{subject}: did not run (exit {rc}) {_first_line(stderr)} — the sandbox is broken, "
            "not strict"
        )
    return Measurement(dimension=dimension, enforced=enforced, detail=detail, exit_code=rc,
                       stderr=_first_line(stderr))


def probe_enforcement(
    provider: ExecutionProvider,
    policy: ExecutionPolicy,
    *,
    workspace: Path,
    outside: Path,
    hidden: Path,
    hidden_file: Path,
    port: int | None = None,
    timeout: float = 120.0,
) -> EnforcementMeasurement:
    """Run each probe through ``provider.confine`` and report what happened.

    ``workspace`` is the directory the policy grants writes to. ``outside`` is a directory
    outside it, ``hidden``/``hidden_file`` a directory and file the policy asks to hide, and
    ``port`` a loopback port with a live listener the caller owns (so a successful connect
    proves network reachability rather than a missing server).
    """

    workspace = Path(workspace)
    outside = Path(outside)
    hidden = Path(hidden)
    hidden_file = Path(hidden_file)
    confined = provider.confine([_python(), "-c", "print('ok')"], policy)
    measurements: list[Measurement] = []

    def run(dimension: str, argv: Sequence[str], *, expect_failure: bool, subject: str) -> None:
        wrapped = provider.confine(argv, policy)
        try:
            result = _run(wrapped.argv, cwd=str(workspace), timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            measurements.append(Measurement(
                dimension=dimension, enforced=None,
                detail=f"{subject}: the probe could not run ({type(exc).__name__}: {exc})",
            ))
            return
        measurements.append(_measure(dimension, result.returncode, result.stdout or "",
                                     result.stderr or "", expect_failure=expect_failure,
                                     subject=subject))

    run("write_inside", _write_probe(workspace / "probe-inside.txt"), expect_failure=False,
        subject="a write inside the workspace")
    (outside / "probe-marker.txt").unlink(missing_ok=True)
    run("write_outside", _write_probe(outside / "probe-marker.txt"), expect_failure=True,
        subject="a write outside the workspace")
    run("read_hidden", _read_probe(hidden_file), expect_failure=True,
        subject=f"a read of {hidden.name}")

    if port is not None:
        run("network", _connect_probe(port), expect_failure=True,
            subject=f"a TCP connect to 127.0.0.1:{port}")
    else:
        measurements.append(Measurement(
            dimension="network", enforced=None,
            detail="no listener was offered, so reachability was not measured",
        ))

    link = workspace / "probe-link"
    target = outside / "probe-link-target"
    target.mkdir(exist_ok=True)
    removed = False
    try:
        if link.exists() or link.is_symlink():
            os.rmdir(link) if os.name == "nt" else link.unlink()
            removed = True
    except OSError:
        pass
    if _junction(link, target):
        run("symlink_escape", _write_probe(link / "probe-escape.txt"), expect_failure=True,
            subject="a write through a link that leaves the workspace")
    else:
        measurements.append(Measurement(
            dimension="symlink_escape", enforced=None,
            detail="a junction/symlink could not be created, so the escape was not measured",
        ))
    _ = removed

    return EnforcementMeasurement(
        provider_id=confined.provider_id,
        enforcement=confined.enforcement,
        mode=str(policy.mode),
        workspace=str(workspace),
        measurements=tuple(measurements),
    )
