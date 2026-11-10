"""The typed seam between an Odessy session and whichever engine runs its turns.

This package is deliberately small: two engines, one protocol, one event envelope and a
handful of named failures. It is not a plugin framework, a second event loop or a
provider registry. Prompt 02 introduces it so that engine selection is a validated
decision made once, at session creation, instead of an attribute read at turn time.

Ownership:
- ``contracts`` — types only. No imports from ``application``.
- ``legacy`` — adapts the existing :class:`~odessy.application.session.SessionController`.
  It contains no agent, model or tool logic.
- ``selection`` — resolves the configured engine and builds its runtime. ``legacy`` is
  the default; every other choice must be registered and available, or construction
  fails with :class:`RuntimeUnavailable` rather than falling back.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, ClassVar, Literal, Mapping, Protocol, runtime_checkable

from odessy.runtime.types import AgentTurnResult

EngineName = Literal["legacy", "harness"]
ENGINES: tuple[str, ...] = ("legacy", "harness")
DEFAULT_ENGINE: str = "legacy"


class RuntimeSelectionFailure(RuntimeError):
    """Base class for runtime-selection failures. Never raised directly."""

    code = "runtime_error"


class RuntimeSelectionError(RuntimeSelectionFailure):
    """The configured engine name is not a known engine (a malformed setting)."""

    code = "runtime_engine_invalid"


class RuntimeUnavailable(RuntimeSelectionFailure):
    """A known engine cannot run in this build/configuration.

    This is always terminal for session creation. Callers must surface it; they must not
    catch it and construct the legacy engine instead.
    """

    code = "runtime_unavailable"

    def __init__(self, engine: str, reason: str, *, detail: str = ""):
        """Initialize the owned state and synchronization used by this component."""
        self.engine = engine
        self.reason = reason
        self.detail = detail
        message = f"Runtime engine {engine!r} is unavailable: {reason}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)

    def public(self) -> dict[str, str]:
        """Return the safe public representation without private runtime state."""
        return {"engine": self.engine, "reason": self.reason, "detail": self.detail}


class RuntimeEngineLocked(RuntimeSelectionFailure):
    """An existing session was asked to move to a different engine."""

    code = "runtime_engine_locked"

    def __init__(self, current: str, requested: str):
        """Initialize the owned state and synchronization used by this component."""
        self.current = current
        self.requested = requested
        super().__init__(
            f"Session engine is {current!r}; a session cannot move to {requested!r}"
        )


class ModelRouteError(RuntimeSelectionFailure):
    """The selected model profile has no faithful expression on this engine.

    Raised instead of substituting a different provider or model: the caller sees a named
    refusal it can show the user, and no turn runs on a route nobody chose.
    """

    code = "model_route_invalid"

    def __init__(self, message: str):
        """Initialize the owned state and synchronization used by this component."""
        self.message = message
        super().__init__(message)

    def public(self) -> dict[str, str]:
        """Return the safe public representation without private runtime state."""
        return {"message": self.message}


class ModelRouteLocked(RuntimeSelectionFailure):
    """A session cannot move to a different provider without being relaunched.

    The child process receives exactly one provider credential, fixed at launch, so a
    provider change is a new process (and, once resume exists, a resumed session) — not a
    silent switch inside a running one. Changing the *model* within the launched provider
    is allowed and in-place.
    """

    code = "model_route_locked"

    def __init__(self, current: str, requested: str):
        """Initialize the owned state and synchronization used by this component."""
        self.current = current
        self.requested = requested
        super().__init__(
            f"This session was launched with the {current!r} provider; {requested!r} needs a "
            "new session (the provider credential is fixed when the engine starts)"
        )

    def public(self) -> dict[str, str]:
        """Return the safe public representation without private runtime state."""
        return {"current": self.current, "requested": self.requested}


class RuntimeCapabilityUnavailable(RuntimeSelectionFailure):
    """A caller asked an engine for something it has not implemented yet.

    This is the runtime half of an honest capability record: the flag is ``False`` and
    the operation raises instead of degrading to a different engine or to a silent
    no-op. ``gate`` names the migration prompt that will implement it.
    """

    code = "runtime_capability_unavailable"

    def __init__(self, capability: str, *, engine: str = "harness", gate: str = ""):
        """Initialize the owned state and synchronization used by this component."""
        self.capability = capability
        self.engine = engine
        self.gate = gate
        message = f"Runtime engine {engine!r} does not implement {capability!r} yet"
        if gate:
            message = f"{message} (arrives with prompt {gate})"
        super().__init__(message)

    def public(self) -> dict[str, str]:
        """Return the safe public representation without private runtime state."""
        return {"engine": self.engine, "capability": self.capability, "gate": self.gate}


@dataclass(frozen=True)
class RuntimeCapabilities:
    """What one engine can do, as a value the application may display or branch on.

    ``False`` means the engine does not provide it. It never means "fall back to the
    other engine". ``harness`` values record the target established by the compatibility
    spike (docs/restructure/harness-integration-contract.md); they are claims about the
    engine, not an availability promise — availability is decided by ``selection``.
    """

    engine: str
    live_text_deltas: bool
    per_session_cancel: bool
    approvals: bool
    questions: bool
    durable_history: bool
    resume: bool
    mcp_tools: bool

    #: Flags a caller may gate on. An unknown name is a programming error, not a
    #: "not supported" answer, so it raises instead of returning False.
    FLAGS: ClassVar[tuple[str, ...]] = (
        "live_text_deltas", "per_session_cancel", "approvals", "questions",
        "durable_history", "resume", "mcp_tools",
    )

    def supports(self, name: str) -> bool:
        """Report whether a named capability is advertised by this engine."""
        if name not in self.FLAGS:
            raise KeyError(f"unknown runtime capability: {name!r}")
        return bool(getattr(self, name))

    def public(self) -> dict[str, Any]:
        """Return the safe public representation without private runtime state."""
        return asdict(self)


LEGACY_CAPABILITIES = RuntimeCapabilities(
    engine="legacy",
    live_text_deltas=False,      # final transcript only; speech streams sentences
    per_session_cancel=True,
    approvals=True,
    questions=True,
    durable_history=False,       # in-memory memory, cleared on close
    resume=False,
    mcp_tools=True,
)

#: What the Harness engine implements **today**, prompt by prompt. Every flag is a
#: claim about running code: a ``False`` flag also makes the operation raise
#: :class:`RuntimeCapabilityUnavailable`, so a caller cannot discover the gap by
#: finding out that nothing happened.
HARNESS_CAPABILITIES = RuntimeCapabilities(
    engine="harness",
    live_text_deltas=False,      # prompt 05 (bridge proven in 01, not wired here)
    per_session_cancel=True,     # prompt 06: ACP session/cancel, one session per child
    approvals=False,             # prompt 08 (ACP session/request_permission)
    questions=False,             # no ACP channel at the pin
    durable_history=False,       # prompt 05/06 (Harness session log not yet replayed)
    resume=False,                # prompt 10
    mcp_tools=False,             # prompts 07-09
)

#: What prompt 01 proved the pinned runtime *can* do. Kept for the migration contract
#: and for the prompts that will flip each flag; never used for gating today.
HARNESS_TARGET_CAPABILITIES = RuntimeCapabilities(
    engine="harness",
    live_text_deltas=True,
    per_session_cancel=True,
    approvals=True,
    questions=False,
    durable_history=True,
    resume=True,
    mcp_tools=True,
)

CAPABILITIES: dict[str, RuntimeCapabilities] = {
    "legacy": LEGACY_CAPABILITIES,
    "harness": HARNESS_CAPABILITIES,
}


_JSON_SCALARS = (str, int, float, bool, type(None))
_MAX_PAYLOAD_DEPTH = 32


def _valid_payload(value: Any, depth: int = 0) -> bool:
    """True for the payload shapes the session actually emits (public() output).

    ``emit()`` stores whatever ``public()`` returns: mappings, lists, strings and JSON
    scalars. Anything else (a set, an object, a non-string key) is not a payload this
    envelope can carry, and is rejected rather than silently replaced.
    """
    if depth > _MAX_PAYLOAD_DEPTH:
        return False
    if isinstance(value, _JSON_SCALARS):
        return True
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _valid_payload(item, depth + 1)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return all(_valid_payload(item, depth + 1) for item in value)
    return False


def _copy_payload(value: Any) -> Any:
    """A detached copy that preserves the payload's own shape, including tuples."""
    if isinstance(value, Mapping):
        return {key: _copy_payload(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_copy_payload(item) for item in value)
    if isinstance(value, list):
        return [_copy_payload(item) for item in value]
    return value


@dataclass(frozen=True)
class RuntimeEvent:
    """One normalized session event, engine independent.

    The shape is exactly the public envelope the API already emits (``id``, ``type``,
    ``time``, ``data``), so wrapping an engine cannot change what a client receives.
    ``from_public``/``public`` are lossless: a payload is preserved as it was given, and
    an unsupported payload raises instead of being discarded.
    """

    id: int
    type: str
    time: float
    data: Any

    @classmethod
    def from_public(cls, event: Mapping[str, Any]) -> "RuntimeEvent":
        """Validate and restore a runtime event from its public envelope."""
        if not isinstance(event, Mapping):
            raise TypeError("runtime events must be mappings")
        event_id = event.get("id")
        event_type = event.get("type")
        if not isinstance(event_id, int) or not isinstance(event_type, str) or not event_type:
            raise ValueError("runtime events require an integer id and a non-empty type")
        data = event["data"] if "data" in event else {}
        if not _valid_payload(data):
            raise TypeError(
                "runtime event data must be JSON-compatible "
                f"(mapping with string keys, list, string or scalar); got {type(data).__name__}"
            )
        return cls(
            id=event_id,
            type=event_type,
            time=float(event.get("time") or 0.0),
            data=_copy_payload(data),
        )

    def public(self) -> dict[str, Any]:
        """Return the safe public representation without private runtime state."""
        return {"id": self.id, "type": self.type, "time": self.time,
                "data": _copy_payload(self.data)}


@dataclass(frozen=True)
class StopOutcome:
    """What a Stop request actually established, in the four states callers need.

    - ``requested`` — a cancellation was sent to the engine.
    - ``execution`` — ``idle`` (nothing was running), ``stopped`` (the engine confirmed the
      turn ended) or ``unconfirmed`` (the bound elapsed: the turn may still be running).
    - ``cleanup`` — ``complete`` when everything this side owned was joined, ``pending``
      when something could not be confirmed within the bound.
    - ``stop_reason`` — the engine's own reason when it reported one.

    ``unconfirmed`` is never a softer word for ``stopped``: it means the outcome is unknown,
    which is also why no Stop ever claims a rollback of work that already happened.
    """

    requested: bool
    execution: str
    cleanup: str
    stop_reason: str | None = None
    detail: str = ""

    #: The only accepted values. A caller can branch on these without string guessing.
    EXECUTIONS: ClassVar[tuple[str, ...]] = ("idle", "stopped", "unconfirmed")
    CLEANUPS: ClassVar[tuple[str, ...]] = ("complete", "pending")

    def __post_init__(self) -> None:
        """Validate the immutable contract before it can cross the runtime boundary."""
        if self.execution not in self.EXECUTIONS:
            raise ValueError(f"unknown stop execution: {self.execution!r}")
        if self.cleanup not in self.CLEANUPS:
            raise ValueError(f"unknown stop cleanup: {self.cleanup!r}")

    @property
    def confirmed(self) -> bool:
        """True only when the engine said the turn ended."""
        return self.execution == "stopped"

    def public(self) -> dict[str, Any]:
        """Return the safe public representation without private runtime state."""
        return {"requested": self.requested, "execution": self.execution,
                "cleanup": self.cleanup, "stop_reason": self.stop_reason,
                "confirmed": self.confirmed, "detail": self.detail}


class TurnCancelled(Exception):
    """The engine confirmed a cancellation; the turn's text is not the answer.

    Raised instead of returning an ``AgentTurnResult`` so no caller can commit a cancelled
    turn's partial answer by accident. ``outcome`` is the same shape as
    :meth:`StopOutcome.public` so an event can carry it unchanged.
    """

    def __init__(self, outcome: Mapping[str, Any] | None = None, *, detail: str = ""):
        """Initialize the owned state and synchronization used by this component."""
        self.outcome = dict(outcome or {})
        self.detail = detail or str(self.outcome.get("detail") or "")
        super().__init__("the turn was cancelled" + (f": {self.detail}" if self.detail else ""))

    def public(self) -> dict[str, Any]:
        """Return the safe public representation without private runtime state."""
        return dict(self.outcome)


@runtime_checkable
class RuntimeSession(Protocol):
    """The minimum an engine exposes for one session.

    Implementations are created once, when the session is created, and never swapped.
    """

    engine: str
    capabilities: RuntimeCapabilities
    session_id: str

    def events(self, after_id: int = 0) -> list[RuntimeEvent]:
        """Bounded replay of public events with ``id > after_id``, oldest first."""

    def snapshot(self) -> dict[str, Any]:
        """The public session snapshot; identical shape for every engine."""

    async def run_turn(self, text: str, *args: Any, **kwargs: Any) -> AgentTurnResult:
        """Run one turn. Signature matches the existing controller's turn entry."""

    async def stop(self, *, timeout: float | None = None) -> "StopOutcome":
        """Stop the running turn and report what was established. Idempotent.

        An engine that cannot confirm the end returns ``execution="unconfirmed"`` rather
        than pretending. ``timeout`` bounds the join; it is not a promise of rollback.
        """

    async def close(self) -> None:
        """Release the session. Idempotent; later turns must fail."""

