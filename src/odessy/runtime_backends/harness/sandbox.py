"""Translate Odessy's execution policy into the pinned Harness sandbox vocabulary.

Why this module exists
----------------------
Odessy's policy has five dimensions: a file-effect *mode*, *read/write roots*, *hidden
credential paths*, *network* policy, and an *isolation requirement* (plus escalation).
The pinned runtime has two: ``mode`` and ``workspaceRoot``. A translation that quietly
dropped the other three would let a session claim a boundary it does not have, so the
translation is written down here, per dimension, with what the pin can be asked for and
what is left to Odessy's own providers.

What the pin actually carries (measured, not assumed)
----------------------------------------------------
``experiments/restructure/harness_compatibility/probe_sandbox_vocabulary.py`` asks the
pinned binary three questions and this module quotes its answers:

* ``--dump-config-schema``: the ``sandbox`` row (``@deepseek-ai/dsh-sandbox-local``)
  accepts ``runnerCommand``/``runnerFailureSignatures``/``probeTimeoutMs``; the
  ``sandbox-policy`` row accepts ``mode`` and ``workspaceRoot``; ``approval`` accepts
  ``policy``; ``permission`` accepts ``defaultPreset``/``presets``. **No row accepts a
  network, hidden-path or extra-writable field.**
* ``--dump-config``: the composed ``sandbox-policy`` row resolves
  ``mode: process.env.DSH_PERMISSION_MODE ?? 'workspace-write'`` and
  ``workspaceRoot: process.cwd()`` — so a launch that sets neither gets
  ``workspace-write`` in whatever directory the child happens to inherit.
* ``session/new`` → ``configOptions``: only ``model`` and ``reasoning_effort``. There is
  **no session option for the sandbox mode**, so one runtime process runs one mode; a
  per-call width change is not expressible at this pin.

The source tree says the same thing structurally: ``SandboxExecutionPolicy`` in
``packages/sandbox/sandbox/src/index.ts`` is ``{mode, workspaceRoot, sessionId?}`` and
the type's own doc says "Network and process visibility are outside this vocabulary".

Consequences this module encodes
--------------------------------
* ``mode`` and the workspace root are **reused** (the names are identical, so the
  translation is the identity function plus two checks).
* ``network="deny"`` and ``hidden_paths`` are **not expressible** at the pin. They stay
  with the Odessy provider that can enforce them (bwrap on Linux), and on a host with no
  such backend they are reported unenforced — or refused outright when the policy says
  isolation is ``required``.
* ``extra_writable`` only ever *adds* a write grant inside the mode, so failing to grant
  it cannot widen authority. It is reported as not granted, and never blocks a launch.
* ``reads`` is carried separately from ``hidden_paths`` on purpose: "this backend cannot
  restrict reads" and "these paths are hidden" are different statements, and the second
  is not implied by the first.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from odessy.config.sandbox import SandboxSettings, load_sandbox_settings
from odessy.execution.contract import (
    MODES,
    ExecutionPolicy,
    IsolationRequirement,
    Mode,
    NetworkPolicy,
)
from odessy.execution.posture import (
    host_posture,
    limitations_when_asked,
    resolve_isolation,
)
from odessy.execution.registry import ProviderRegistry, host_registry
from odessy.runtime_backends.contracts import RuntimeUnavailable

__all__ = [
    "BOUNDARIES",
    "HARNESS_CARRIES",
    "HARNESS_LACKS",
    "PIN",
    "SANDBOX_PATCH_PREFIX",
    "Dimension",
    "SandboxPlan",
    "SandboxRequirementRefused",
    "explicit_plan",
    "materialize_sandbox_patch",
    "plan_for_session",
    "translate",
]

ENGINE = "harness"

#: Session-specific overlays live in the Odessy-owned Harness home: the runtime takes
#: ``--patch <file>`` and the content is source, not a shipped asset type. One file per plan
#: (content-addressed), never one shared file, so concurrent sessions cannot collide.
SANDBOX_PATCH_PREFIX = "odessy_sandbox-"

#: The pin whose vocabulary this module was written against. Changing it means re-running
#: ``probe_sandbox_vocabulary.py`` before trusting any row below.
PIN = "deepseek-harness-sdk 0.2.1rc2 / deepseek-harness-runtime-bin 0.2.1rc2"

#: What the pin's confinement seam can be asked to enforce.
HARNESS_CARRIES: tuple[str, ...] = ("mode", "workspace_root")

#: Odessy dimensions the pin has no vocabulary for, with the measured reason. A reason
#: ending in "(measured)" is quoted from the probe report; the rest is structural.
HARNESS_LACKS: Mapping[str, str] = {
    "network": (
        "no row accepts a network field and SandboxExecutionPolicy carries none; the "
        "bwrap profile does not unshare the network (measured)"
    ),
    "hidden_paths": (
        "no row accepts a hidden path; the profile binds the host root read-only rather "
        "than masking a path (measured)"
    ),
    "extra_writable": (
        "no row accepts an extra writable root; a backend grants the workspace and its "
        "own temp area only (measured)"
    ),
    "per_call_mode": (
        "session/new offers only model and reasoning_effort, so one runtime process runs "
        "one mode (measured)"
    ),
}

#: Escalation settings, in ``sandbox.toml``'s vocabulary.
ESCALATION_SETTINGS = ("ask", "never", "workspace-write-only")

#: Where the plan is applied. ``odessy`` means Odessy's own execution layer wraps the command
#: with a provider that can enforce the dimension. ``harness`` means the pinned runtime is
#: the boundary, and its vocabulary stops at ``mode``/``workspaceRoot`` — so a dimension it
#: cannot express is **unavailable here**, no matter that some host provider supports it.
BOUNDARIES = ("harness", "odessy")


class SandboxRequirementRefused(RuntimeUnavailable):
    """The host cannot enforce a dimension the policy *required*.

    A subclass of :class:`~odessy.runtime_backends.contracts.RuntimeUnavailable` because
    the outcome is the same: this engine cannot serve this session. It is a separate type
    so a caller can tell "the boundary is missing" from "the binary is missing".
    """

    def __init__(self, plan: "SandboxPlan") -> None:
        missing = ", ".join(plan.unenforced) or "confinement"
        super().__init__(
            ENGINE,
            "the session's sandbox policy requires an absolute boundary this host cannot "
            f"provide ({missing})",
            detail=plan.refusal or "",
        )
        self.plan = plan


@dataclass(frozen=True)
class Dimension:
    """One dimension of the policy, and who enforces it under this plan."""

    name: str
    requested: bool
    harness: bool
    enforced: bool
    provider: str | None
    evidence: str


@dataclass(frozen=True)
class SandboxPlan:
    """The resolved confinement for one target, in both vocabularies.

    The plan is the single answer the launch, the approval prompt, the audit record and
    the report read. ``harness`` is what the runtime is told; ``retained`` is what stays
    with an Odessy provider because the pin cannot express it.
    """

    target: str
    mode: Mode
    workspace_root: str
    network: NetworkPolicy
    hidden_paths: tuple[str, ...]
    extra_writable: tuple[str, ...]
    reads: str
    isolation: IsolationRequirement
    escalation: str
    dimensions: tuple[Dimension, ...]
    unenforced: tuple[str, ...]
    refusal: str | None = None
    provider: str | None = None
    enforcement: str = "none"
    measurement: str = "declared"
    #: Which boundary this plan describes (``BOUNDARIES``). A dimension the boundary cannot
    #: express is reported unenforced even when a host provider supports it.
    boundary: str = "odessy"

    # ------------------------------------------------------------------ queries

    def dimension(self, name: str) -> Dimension | None:
        return next((item for item in self.dimensions if item.name == name), None)

    def enforced(self, name: str) -> bool:
        found = self.dimension(name)
        return bool(found and found.enforced)

    @property
    def launchable(self) -> bool:
        return self.refusal is None

    def require_launchable(self) -> None:
        """Raise when a *required* dimension is unenforced; never downgrade silently."""

        if self.refusal is not None:
            raise SandboxRequirementRefused(self)

    # ------------------------------------------------------------- projections

    def environment(self) -> dict[str, str]:
        """What the child needs so the pin's own default cannot decide the mode.

        ``sandbox-policy`` reads ``DSH_PERMISSION_MODE``; the overlay below sets the mode
        explicitly as well. Both are emitted because the pin's ``approval.policy``
        expression is derived from this variable, and a value that disagreed with the
        overlay would make the approval policy disagree with the sandbox.
        """

        return {"DSH_PERMISSION_MODE": self.mode}

    def execution_arguments(self) -> dict[str, Any]:
        """``{network, hidden_paths, extra_writable}`` for the Odessy execution layer.

        The same values Odessy already passes per call to its own providers, produced from
        this plan so the two sides cannot drift.
        """

        arguments: dict[str, Any] = {
            "network": self.network,
            "hidden_paths": list(self.hidden_paths),
        }
        if self.extra_writable:
            arguments["extra_writable"] = list(self.extra_writable)
        return arguments

    def execution_policy(self) -> ExecutionPolicy:
        """The plan as the execution contract's own shape."""

        return ExecutionPolicy(
            mode=self.mode,
            workspace_root=self.workspace_root,
            network=self.network,
            hidden_paths=self.hidden_paths,
            extra_writable=self.extra_writable,
        )

    def patch_text(self) -> str:
        """The overlay that pins the two fields the pin accepts.

        Rows: ``sandbox-policy`` (mode + root) and ``approval`` (ask). The approval policy
        is set explicitly so the pin's ``DSH_PERMISSION_MODE``-derived expression cannot
        decide it. No tool row is named: the narrow overlay still owns the tool surface.
        """

        return (
            "# Odessy sandbox policy (prompt 08). Generated from "
            "odessy/runtime_backends/harness/sandbox.py; edit that module, not this file.\n"
            "# The pinned runtime accepts exactly two confinement fields: mode and\n"
            "# workspaceRoot. Network, hidden paths and extra writable roots have no\n"
            "# vocabulary here and stay with Odessy's own providers.\n"
            "- id: sandbox-policy\n"
            "  config:\n"
            f"    mode: {_yaml_scalar(self.mode)}\n"
            f"    workspaceRoot: {_yaml_scalar(self.workspace_root)}\n"
            "- id: approval\n"
            "  config:\n"
            f"    policy: {_yaml_scalar('ask')}\n"
        )

    def describe(self) -> dict[str, object]:
        """A recordable summary. Paths are included; no credential value ever is."""

        return {
            "target": self.target,
            "mode": self.mode,
            "workspace_root": self.workspace_root,
            "network": self.network,
            "hidden_paths": len(self.hidden_paths),
            "reads": self.reads,
            "isolation": self.isolation,
            "escalation": self.escalation,
            "boundary": self.boundary,
            "provider": self.provider,
            "enforcement": self.enforcement,
            "unenforced": list(self.unenforced),
            "measurement": self.measurement,
            "refused": self.refusal,
        }

    # --------------------------------------------------------------- derivation

    def with_measurement(self, *, verified: bool) -> "SandboxPlan":
        """Mark whether the enforcement claim was measured on this host or declared."""

        return replace(self, measurement="measured" if verified else "declared")


def _yaml_scalar(value: str) -> str:
    """A single-quoted YAML scalar (backslashes literal, embedded quotes doubled)."""

    return "'" + value.replace("'", "''") + "'"


def _retained_evidence(limitation: str, verb: str, *, requested: bool, supported: bool,
                       boundary: str, provider: str | None) -> str:
    """One sentence per dimension: supported by the host, and actually applied where?"""

    if not requested:
        return "not requested under this mode"
    if not supported:
        return f"requested, but the effective backend reports {limitation}"
    if boundary == "odessy":
        return f"{provider} {verb} when the call asks"
    return (
        f"a host backend ({provider}) can {verb.split(' ', 1)[-1]}, but the pinned runtime "
        "has no vocabulary for it and nothing applies it at this boundary; counted as "
        "unavailable until prompt 09 wires the call path"
    )


def _plan_digest(plan: SandboxPlan) -> str:
    """A short digest of the fields the overlay carries, so artifacts are content-addressed."""

    payload = f"{plan.mode}\x00{plan.workspace_root}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def materialize_sandbox_patch(home: Path, plan: SandboxPlan) -> Path:
    """Write *this plan's* overlay into the Harness home and return its path.

    The file is content-addressed (``odessy_sandbox-<digest>.patch.yml``) and written through
    a unique temporary name, because two sessions created at the same time have different
    modes or workspaces and must not share one artifact: a fixed name plus a fixed ``.tmp``
    path lets one session's write land in another session's launch.
    """

    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    target = home / f"{SANDBOX_PATCH_PREFIX}{_plan_digest(plan)}.patch.yml"
    text = plan.patch_text()
    try:
        if target.read_text(encoding="utf-8") == text:
            return target
    except OSError:
        pass
    handle, temporary = tempfile.mkstemp(dir=home, prefix=".odessy-sandbox-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target


def _requested(dimension: str, *, mode: Mode, network: NetworkPolicy,
               hidden_paths: tuple[str, ...], extra_writable: tuple[str, ...]) -> bool:
    if dimension == "network":
        return network == "deny" and mode != "danger-full-access"
    if dimension == "hidden_paths":
        return bool(hidden_paths) and mode != "danger-full-access"
    if dimension == "extra_writable":
        return bool(extra_writable) and mode != "danger-full-access"
    if dimension == "mode":
        return True
    raise KeyError(dimension)


def translate(
    *,
    workspace: str | os.PathLike[str],
    mode: Mode,
    network: NetworkPolicy,
    hidden_paths: Iterable[str] = (),
    extra_writable: Iterable[str] = (),
    isolation: IsolationRequirement = "preferred",
    escalation: str = "ask",
    target: str = "local",
    limitations: Iterable[str] = (),
    provider: str | None = None,
    enforcement: str = "none",
    measurement: str = "declared",
    boundary: str = "odessy",
) -> SandboxPlan:
    """Turn one policy into a plan, naming who enforces each dimension.

    ``limitations`` is what the effective host backend reports **when asked** under this
    policy (:func:`odessy.execution.posture.limitations_when_asked`), not what it does by
    default: a backend that cuts the network whenever a call asks is not limited here
    merely because its default call does not ask.

    The function is pure. It never probes, never spawns, and never widens: an unknown mode
    is a ``ValueError`` rather than a default.
    """

    if mode not in MODES:
        raise ValueError(f"unknown sandbox mode {mode!r}; expected one of {list(MODES)}")
    if isolation not in ("required", "preferred", "not-required"):
        raise ValueError(f"unknown isolation requirement {isolation!r}")
    if escalation not in ESCALATION_SETTINGS:
        raise ValueError(f"unknown escalation setting {escalation!r}")
    if boundary not in BOUNDARIES:
        raise ValueError(f"unknown enforcement boundary {boundary!r}")

    hidden = tuple(str(Path(item).expanduser()) for item in hidden_paths)
    extra = tuple(str(Path(item).expanduser()) for item in extra_writable)
    gaps = frozenset(limitations)

    # The pin's own confinement seam: a confined mode is carried, danger-full-access asks
    # for no confinement at all, and "no backend" is not enforcement.
    confining = mode != "danger-full-access" and provider not in (None, "none", "unconfined")
    reads = (
        "unrestricted"
        if not confining or "cannot_restrict_reads" in gaps
        else "governed-by-mode"
    )

    dimensions: list[Dimension] = []
    for name in ("mode", "network", "hidden_paths", "extra_writable"):
        requested = _requested(name, mode=mode, network=network, hidden_paths=hidden,
                               extra_writable=extra)
        if name == "mode":
            dimensions.append(Dimension(
                name=name, requested=True, harness=True, enforced=confining,
                provider=provider if confining else None,
                evidence=(
                    f"reused unchanged: the pin's mode vocabulary is {list(MODES)} and the "
                    f"profile row accepts it (measured, {PIN})"
                ) if confining else "no confining backend is usable on this host",
            ))
            continue
        harness = False
        if name == "network":
            supported = requested and confining and "cannot_restrict_network" not in gaps
            enforced = supported and boundary == "odessy"
            evidence = _retained_evidence("cannot_restrict_network", "cut the network",
                                          requested=requested, supported=supported,
                                          boundary=boundary, provider=provider)
            who = provider if enforced else None
        elif name == "hidden_paths":
            supported = requested and confining and "cannot_hide_paths" not in gaps
            enforced = supported and boundary == "odessy"
            evidence = _retained_evidence("cannot_hide_paths", "mask the requested paths",
                                          requested=requested, supported=supported,
                                          boundary=boundary, provider=provider)
            who = provider if enforced else None
        else:  # extra_writable
            # Additive inside the write grant, never a boundary: not granting it cannot
            # widen authority, so it is reported rather than refused.
            enforced = False
            evidence = "additive inside the write grant; not granted by this backend"
            who = None
        dimensions.append(Dimension(
            name=name, requested=requested, harness=harness, enforced=enforced,
            provider=who, evidence=evidence,
        ))

    unenforced = tuple(
        item.name for item in dimensions
        if item.requested and not item.enforced
        # ``extra_writable`` only ever adds a write grant, and ``danger-full-access`` asks
        # for no confinement at all: neither is a gap in the boundary.
        and item.name not in ("extra_writable", "mode")
    )
    if mode != "danger-full-access" and not confining:
        # A confined mode with no usable backend *is* the gap, and it blocks ``required``.
        unenforced = ("mode", *unenforced)

    refusal: str | None = None
    if isolation == "required" and mode == "danger-full-access":
        refusal = (
            "isolation=required contradicts mode=danger-full-access: the mode requests no "
            "confinement, so no boundary can be required of it"
        )
    elif isolation == "required" and unenforced:
        missing = ", ".join(
            f"{name} ({HARNESS_LACKS.get(name, 'not enforced by any usable backend')})"
            for name in unenforced
        )
        refusal = (
            "isolation=required, and this host cannot enforce: "
            f"{missing}. Refusing to run is the only honest outcome."
        )

    plan = SandboxPlan(
        target=target,
        mode=mode,
        workspace_root=str(Path(workspace).expanduser().resolve()),
        network=network,
        hidden_paths=hidden,
        extra_writable=extra,
        reads=reads,
        isolation=isolation,
        escalation=escalation,
        dimensions=tuple(dimensions),
        unenforced=unenforced,
        refusal=refusal,
        provider=provider,
        enforcement=enforcement,
        measurement=measurement,
        boundary=boundary,
    )
    if refusal is not None:
        plan.require_launchable()
    return plan


def _isolation_from(environ: Mapping[str, str], settings: SandboxSettings,
                    target: str) -> str:
    """``ODESSY_ISOLATION``, then the target's own setting, then the global one."""

    explicit = (environ.get("ODESSY_ISOLATION") or "").strip().lower()
    if explicit:
        if explicit not in ("auto", "required", "preferred", "not-required"):
            raise RuntimeUnavailable(
                ENGINE, "ODESSY_ISOLATION is not a known requirement", detail=explicit
            )
        return explicit
    return settings.isolation_setting_for(target) or settings.isolation or "auto"


def plan_for_session(
    *,
    workspace: str | os.PathLike[str],
    target: str = "local",
    mode: Mode = "workspace-write",
    settings: SandboxSettings | None = None,
    isolation: IsolationRequirement | None = None,
    registry: ProviderRegistry | None = None,
    environ: Mapping[str, str] | None = None,
    boundary: str = "harness",
) -> SandboxPlan:
    """The plan this host can offer for one session, from configuration only.

    ``mode`` defaults to ``workspace-write``: the widest mode the authority layer can
    grant without escalation, which is the outer boundary the sandbox owes the session.
    Narrowing to ``read-only`` per action is *authorization*, which the policy already
    does, and which the pin cannot express per call (``per_call_mode`` above).
    """

    environ = os.environ if environ is None else environ
    settings = settings if settings is not None else load_sandbox_settings(environ)
    registry = registry if registry is not None else host_registry()
    workspace_path = str(Path(workspace).expanduser())

    requirement: IsolationRequirement | str = isolation or resolve_isolation(
        _isolation_from(environ, settings, target), registry, workspace_path, target=target
    )
    if requirement not in ("required", "preferred", "not-required"):
        raise RuntimeUnavailable(
            ENGINE, "the isolation requirement is unknown", detail=str(requirement)
        )

    network = settings.network_for(mode)
    hidden = settings.hidden_paths
    extra = settings.extra_writable
    escalation = settings.escalation_for(target)

    # What the backend this host would actually select reports *when asked*. A target this
    # side cannot probe (the paired laptop) is assumed unable rather than assumed capable.
    provider: str | None = None
    enforcement = "none"
    limitations: tuple[str, ...]
    if target != "local":
        limitations = ("cannot_hide_paths", "cannot_restrict_network")
    else:
        posture = {item.mode: item for item in host_posture(registry, workspace_path)}
        current = posture.get(mode)
        if current is None:
            limitations = ("cannot_hide_paths", "cannot_restrict_network")
        else:
            provider = current.provider_id
            enforcement = current.enforcement
            limitations = limitations_when_asked(registry, current, workspace_path)

    return translate(
        workspace=workspace_path,
        mode=mode,
        network=network,
        hidden_paths=hidden,
        extra_writable=extra,
        isolation=requirement,  # type: ignore[arg-type]
        escalation=escalation,
        target=target,
        limitations=limitations,
        provider=provider,
        enforcement=enforcement,
        boundary=boundary,
    )


#: The narrowest plan this module will produce on request: workspace-write in the given
#: root, network allowed, nothing hidden. Only :func:`plan_for_session` produces a plan the
#: product uses; this exists so a caller that injects an explicit ``HarnessConfig`` (tests,
#: integration smoke) has to state a mode rather than inherit a permissive default by
#: omission.
def explicit_plan(
    workspace: str | os.PathLike[str],
    *,
    mode: Mode = "workspace-write",
    network: NetworkPolicy = "allow",
    hidden_paths: Iterable[str] = (),
    extra_writable: Iterable[str] = (),
    isolation: IsolationRequirement = "preferred",
    escalation: str = "ask",
    target: str = "local",
    limitations: Iterable[str] = ("cannot_restrict_network", "cannot_hide_paths"),
    provider: str | None = "unconfined",
    enforcement: str = "none",
    boundary: str = "harness",
) -> SandboxPlan:
    """A plan for an injected configuration, with every field stated by the caller."""

    return translate(
        workspace=workspace,
        mode=mode,
        network=network,
        hidden_paths=hidden_paths,
        extra_writable=extra_writable,
        isolation=isolation,
        escalation=escalation,
        target=target,
        limitations=limitations,
        provider=provider,
        enforcement=enforcement,
        boundary=boundary,
    )
