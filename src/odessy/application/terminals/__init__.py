"""Default packaged capability stays off until 07 wires the opt-in native owner.

A real PTY needs a reviewed native dependency or binding. This project has no cached
``node-pty``/``@lydell/node-pty``/``koffi``/``winpty`` and no reviewed ConPTY
adaptation, so the service refuses to create a terminal instead of dressing recorded
tool output up as a shell. Nothing here spawns a process.
"""
from __future__ import annotations

from typing import Any, Mapping

from .service import LocalTerminalService, TerminalError

#: One stable, specific reason string; 07 exposes it in capabilities/UI copy.
PTY_REASON = (
    'Interactive terminal unavailable: no reviewed PTY dependency is installed. '
    'The build has no node-pty/@lydell-node-pty/koffi/winpty package and no reviewed '
    'ConPTY binding, so no local shell can be started. Recorded command output is not '
    'a terminal.'
)


class PtyUnavailable(RuntimeError):
    """Raised by every terminal operation; carries the HTTP status 07 should map."""

    status = 501
    code = 'unsupported'

    def __init__(self, reason: str = PTY_REASON) -> None:
        super().__init__(reason)
        self.reason = reason


def pty_availability(environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """No probing of a shell: the answer is a packaging fact, not a runtime guess."""
    return {
        'available': False,
        'reason': PTY_REASON,
        'dependency': 'node-pty|@lydell/node-pty|koffi|winpty|conpty-binding',
        'recorded_output_is_not_terminal': True,
        'remote_terminal': False,
    }


class TerminalService:
    """Refuses every operation until a real PTY backend is separately authorized.

    The method set matches the frozen contract so that 07 can wire routes against one
    object without inventing a second API; each call fails closed.
    """

    def __init__(self, *, backend: Any = None) -> None:
        self._backend = backend

    def capability(self) -> dict[str, Any]:
        return pty_availability()

    @property
    def available(self) -> bool:
        return False

    def _refuse(self):
        raise PtyUnavailable()

    def create(self, sid: str, *, columns: int = 80, rows: int = 24) -> dict[str, Any]:
        self._refuse()

    def output(self, sid: str, handle: str, *, after: int = 0) -> dict[str, Any]:
        self._refuse()

    def input(self, sid: str, handle: str, *, text: str, expected_revision: int) -> dict[str, Any]:
        self._refuse()

    def resize(self, sid: str, handle: str, *, columns: int, rows: int,
               expected_revision: int) -> dict[str, Any]:
        self._refuse()

    def interrupt(self, sid: str, handle: str, *, expected_revision: int) -> dict[str, Any]:
        self._refuse()

    def close(self, sid: str, handle: str, *, expected_revision: int) -> dict[str, Any]:
        self._refuse()

    def close_session(self, sid: str) -> None:
        """No owned processes exist, so nothing needs termination."""
        return None


def register(app: Any, dependencies: Any, *, service: TerminalService | None = None) -> None:
    """Deliberately registers no route.

    Mounting a create endpoint that always fails would invite a client to render a
    terminal surface that cannot work. The honest state is "capability false" plus the
    reason string, which 07 exposes through the capabilities payload.
    """
    bound = service or TerminalService()
    app.state.terminals = bound
    return None
