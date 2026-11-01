"""What this host's sandbox can do, and the isolation ``auto`` resolves to.

Shared by the composition root (which resolves ``ODESSY_ISOLATION=auto`` once per
process) and the doctor (which prints the same answer), so the two cannot disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

from odessy.execution.contract import (
    CONFINED_MODES,
    ExecutionPolicy,
    IsolationRequirement,
    Requirement,
    UnsatisfiableRequirementError,
)
from odessy.execution.registry import ProviderRegistry

__all__ = ["ModePosture", "full_isolation_available", "host_posture", "resolve_isolation"]


@dataclass(frozen=True)
class ModePosture:
    """The backend one confined mode gets on this host, under ``preferred``."""

    mode: str
    provider_id: str
    enforcement: str
    limitations: tuple[str, ...]
    #: Whether ``required`` would be met for this mode.
    required_ok: bool


def _select(registry: ProviderRegistry, mode: str, workspace: str, isolation: IsolationRequirement):
    return registry.select(Requirement(policy=ExecutionPolicy(mode=mode, workspace_root=workspace), isolation=isolation))


def full_isolation_available(registry: ProviderRegistry, workspace: str) -> bool:
    """Whether both confined modes can be fully enforced here."""

    for mode in CONFINED_MODES:
        try:
            _select(registry, mode, workspace, "required")
        except UnsatisfiableRequirementError:
            return False
    return True


def host_posture(registry: ProviderRegistry, workspace: str) -> list[ModePosture]:
    postures = []
    for mode in CONFINED_MODES:
        try:
            required_ok = bool(_select(registry, mode, workspace, "required"))
        except UnsatisfiableRequirementError:
            required_ok = False
        try:
            selection = _select(registry, mode, workspace, "preferred")
            postures.append(ModePosture(mode, selection.provider.provider_id, selection.evaluation.enforcement,
                                        tuple(sorted(selection.evaluation.limitations)), required_ok))
        except UnsatisfiableRequirementError:
            postures.append(ModePosture(mode, "none", "none", (), required_ok))
    return postures


def resolve_isolation(setting: str, registry: ProviderRegistry, workspace: str, *, target: str = "local") -> IsolationRequirement:
    """``auto`` becomes ``required`` where this host can fully confine both modes,
    and ``preferred`` elsewhere. The paired laptop is a different host this side
    cannot probe, so ``auto`` is ``preferred`` there; set it in ``[targets.laptop]``."""

    if setting != "auto":
        return setting  # type: ignore[return-value]
    if target != "local":
        return "preferred"
    return "required" if full_isolation_available(registry, workspace) else "preferred"
