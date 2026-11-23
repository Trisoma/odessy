"""HTTP routes for the breakpoint timeline and task-level undo.

Registration contract: ``register(app, dependencies, *, service)``. Like the
conversation routes, listing and undo here never construct an engine; undo may
request an approved file rollback through the injected checkpoint owner.

These routes are backend only — the desktop UI is intentionally out of scope
for this change.
"""
import asyncio
from typing import Any

from .service import BreakpointService, Unsupported
from ..conversations.store import Conflict, Invalid, NotFound, StorageError, _check_int


def _detail(exc: StorageError) -> dict[str, str]:
    return {'code': exc.code, 'message': str(exc)}


def register(app, dependencies, *, service: BreakpointService) -> None:
    from fastapi import HTTPException, Request

    if not isinstance(service, BreakpointService):
        raise TypeError('service must be a BreakpointService')

    def fail(exc: StorageError) -> HTTPException:
        return HTTPException(status_code=exc.status, detail=_detail(exc))

    async def json_body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, {'code': 'invalid', 'message': 'Expected a JSON object'})
        if not isinstance(data, dict):
            raise HTTPException(400, {'code': 'invalid', 'message': 'Expected a JSON object'})
        return data

    @app.get('/v1/conversations/{cid}/breakpoints')
    async def list_breakpoints(cid: str, limit: str | None = None):
        try:
            return await asyncio.to_thread(
                service.list, cid, limit=_int_arg(limit, 'limit', 200, minimum=1, maximum=500))
        except (Invalid, NotFound, StorageError) as exc:
            raise fail(exc) from None

    @app.post('/v1/conversations/{cid}/undo')
    async def undo_conversation(cid: str, request: Request):
        data = await json_body(request)
        unknown = set(data) - {'breakpoint_id', 'expected_revision', 'session_id'}
        if unknown:
            raise HTTPException(400, {'code': 'invalid',
                                      'message': 'Unknown fields: ' + ', '.join(sorted(unknown))})
        if not isinstance(data.get('breakpoint_id'), str):
            raise HTTPException(400, {'code': 'invalid', 'message': 'breakpoint_id is required'})
        rev = data.get('expected_revision')
        if isinstance(rev, bool) or not isinstance(rev, int):
            raise HTTPException(400, {'code': 'invalid',
                                      'message': 'expected_revision must be an integer'})
        sid = data.get('session_id')
        if sid is not None and not isinstance(sid, str):
            raise HTTPException(400, {'code': 'invalid', 'message': 'session_id must be a string'})
        try:
            if sid:
                # A live session id enables the approved file rollback path.
                return await service.undo_async(
                    cid, data['breakpoint_id'], expected_revision=rev, session_id=sid)
            return await asyncio.to_thread(
                service.undo, cid, data['breakpoint_id'],
                expected_revision=rev, session_id=sid)
        except (Invalid, NotFound, Conflict, Unsupported, StorageError) as exc:
            raise fail(exc) from None


def create_registrar(service: BreakpointService):
    """Return a registrar usable as an AgentBasics registrar."""
    def registrar(app, dependencies):
        register(app, dependencies, service=service)
    return registrar


def _int_arg(value: Any, name: str, default: int, *, minimum: int = 0,
             maximum: int | None = None) -> int:
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        raise Invalid(f'{name} must be an integer')
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise Invalid(f'{name} must be an integer') from None
    if parsed < minimum or (maximum is not None and parsed > maximum):
        raise Invalid(f'{name} must be in range')
    return parsed
