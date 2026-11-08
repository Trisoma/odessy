"""Adapter that exposes the existing session controller as a ``RuntimeSession``.

Nothing here decides what a turn does. Every method delegates to the controller that
already owns the model, agent, tools, approvals, collaboration, voice callbacks and
event buffer, so behaviour is unchanged by construction: this module adds a seam, not
an implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .contracts import LEGACY_CAPABILITIES, RuntimeCapabilities, RuntimeEvent

if TYPE_CHECKING:  # pragma: no cover - typing only
    from odessy.application.session import SessionController


class LegacyRuntime:
    """The current engine, wrapped without rewriting any of it."""

    engine = "legacy"
    capabilities: RuntimeCapabilities = LEGACY_CAPABILITIES

    def __init__(self, session: "SessionController"):
        self._session = session

    @property
    def session_id(self) -> str:
        return self._session.id

    def events(self, after_id: int = 0) -> list[RuntimeEvent]:
        return [
            RuntimeEvent.from_public(event)
            for event in self._session.events
            if int(event.get("id", -1)) > after_id
        ]

    def snapshot(self) -> dict[str, Any]:
        return self._session.public_snapshot()

    async def run_turn(self, text: str, *args: Any, **kwargs: Any):
        return await self._session.run_turn(text, *args, **kwargs)

    async def stop(self) -> None:
        await self._session.stop()

    async def close(self) -> None:
        await self._session.close()
