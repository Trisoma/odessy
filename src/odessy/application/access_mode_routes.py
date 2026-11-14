"""Standalone HTTP routes for session access modes.

The integrator mounts this registrar through ``AgentBasics``; it inherits the existing bearer
authentication and live session lookup and adds no session semantics of its own. Only the two
frozen routes exist: reading never mutates, and writing requires the current revision plus the
service identity that issued it.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from .access_modes import AccessModeError, AccessModeService

_CODES = {
    400: 'invalid_request',
    403: 'denied',
    404: 'not_found',
    409: 'conflict',
    413: 'too_large',
    501: 'unsupported',
    503: 'unavailable',
}

_FIELDS = frozenset({'mode', 'expected_revision', 'service_instance_id'})


def register(app: Any, dependencies: Any, *, service: AccessModeService | None = None) -> None:
    """Mount GET/PUT /v1/sessions/{sid}/access-mode.

    ``service`` must be the same instance the trusted dispatcher uses for review/validate;
    otherwise a mode could be set that never governs an action.
    """
    bound = service if service is not None else AccessModeService(dependencies)
    if not isinstance(bound, AccessModeService):
        raise TypeError('service must be an AccessModeService')
    app.state.access_modes = bound

    def fail(error: AccessModeError):
        raise HTTPException(
            error.code,
            detail={'code': _CODES.get(error.code, 'error'), 'message': error.message},
        )

    def invalid(message: str):
        raise HTTPException(400, detail={'code': 'invalid_request', 'message': message})

    @app.get('/v1/sessions/{sid}/access-mode')
    async def access_mode(sid: str):
        try:
            return bound.snapshot(sid)
        except AccessModeError as error:
            fail(error)

    @app.put('/v1/sessions/{sid}/access-mode')
    async def update_access_mode(sid: str, request: Request):
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            invalid('Expected a JSON object')
        if not isinstance(data, dict):
            invalid('Expected a JSON object')
        unknown = sorted(set(data) - _FIELDS)
        if unknown:
            invalid('Unknown access-mode fields: ' + ', '.join(unknown))
        missing = sorted(_FIELDS - set(data))
        if missing:
            invalid('Missing access-mode fields: ' + ', '.join(missing))
        expected = data['expected_revision']
        if isinstance(expected, bool) or not isinstance(expected, int):
            invalid('expected_revision must be an integer, not a boolean or other type')
        try:
            return bound.set_mode(
                sid,
                mode=data['mode'],
                expected_revision=expected,
                service_instance_id=data['service_instance_id'],
            )
        except AccessModeError as error:
            fail(error)
