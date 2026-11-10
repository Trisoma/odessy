"""Adapter that exposes the existing session controller as a ``RuntimeSession``.

Nothing here decides what a turn does. Every method delegates to the controller that
already owns the model, agent, tools, approvals, collaboration, voice callbacks and
event buffer, so behaviour is unchanged by construction: this module adds a seam, not
an implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .contracts import LEGACY_CAPABILITIES, RuntimeCapabilities, RuntimeEvent, StopOutcome

if TYPE_CHECKING:  # pragma: no cover - typing only
    from odessy.application.session import SessionController


class LegacyRuntime:
    """The current engine, wrapped without rewriting any of it."""

    engine = "legacy"
    capabilities: RuntimeCapabilities = LEGACY_CAPABILITIES

    def __init__(self, session: "SessionController"):
        """Initialize the owned state and synchronization used by this component."""
        self._session = session

    @property
    def session_id(self) -> str:
        """Return the application session identity owned by this adapter."""
        return self._session.id

    def events(self, after_id: int = 0) -> list[RuntimeEvent]:
        """Return normalized events after the requested cursor."""
        return [
            RuntimeEvent.from_public(event)
            for event in self._session.events
            if int(event.get("id", -1)) > after_id
        ]

    def snapshot(self) -> dict[str, Any]:
        """Return the application projection without creating a second runtime history."""
        return self._session.public_snapshot()

    async def run_turn(self, text: str, *args: Any, **kwargs: Any):
        """Delegate one admitted input to the bound runtime and return its settled result."""
        return await self._session.run_turn(text, *args, **kwargs)

    async def stop(self, *, timeout: float | None = None) -> StopOutcome:
        """Delegate to the controller's own stop and report what it established."""
        outcome = await self._session.stop()
        if isinstance(outcome, StopOutcome):
            return outcome
        outcome = outcome if isinstance(outcome, dict) else {}
        return StopOutcome(
            requested=bool(outcome.get("requested")),
            execution=str(outcome.get("execution") or "idle"),
            cleanup=str(outcome.get("cleanup") or "complete"),
            stop_reason=outcome.get("stop_reason"),
            detail=str(outcome.get("detail") or ""),
        )

    async def close(self) -> None:
        """Release this component and its owned resources; repeated closure is safe."""
        await self._session.close()
