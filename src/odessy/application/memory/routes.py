"""HTTP routes for explicit project memory (B11)."""
import asyncio

from .service import MemoryService
from ..context.wire import detail, only
from ..conversations.store import Invalid, StorageError


def register(app, dependencies, *, service: MemoryService) -> None:
    from fastapi import HTTPException, Request
    from fastapi.responses import Response

    def fail(exc: StorageError):
        return HTTPException(status_code=exc.status, detail=detail(exc))

    async def json_body(request: Request):
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, {'code': 'invalid', 'message': 'Expected a JSON object'})
        if not isinstance(data, dict):
            raise HTTPException(400, {'code': 'invalid', 'message': 'Expected a JSON object'})
        return data

    async def call(function, *args, **kwargs):
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except StorageError as exc:
            raise fail(exc) from None

    @app.get('/v1/projects/{project_id}/memory')
    async def list_memory(project_id: str):
        items = await call(service.list, project_id)
        return {'version': 1, 'items': items}

    @app.post('/v1/projects/{project_id}/memory', status_code=201)
    async def create_memory(project_id: str, request: Request):
        data = await json_body(request)
        try:
            only(data, {'text'})
        except Invalid as exc:
            raise fail(exc) from None
        if 'text' not in data:
            raise HTTPException(400, {'code': 'invalid', 'message': 'text is required'})
        return await call(service.create, project_id, data['text'])

    @app.patch('/v1/projects/{project_id}/memory/{memory_id}')
    async def patch_memory(project_id: str, memory_id: str, request: Request):
        data = await json_body(request)
        try:
            only(data, {'expected_revision', 'text', 'enabled'})
        except Invalid as exc:
            raise fail(exc) from None
        if 'expected_revision' not in data:
            raise HTTPException(400, {'code': 'invalid',
                                      'message': 'expected_revision is required'})
        return await call(service.patch, project_id, memory_id,
                          expected_revision=data['expected_revision'],
                          text=data.get('text'), enabled=data.get('enabled'))

    @app.delete('/v1/projects/{project_id}/memory/{memory_id}', status_code=204)
    async def delete_memory(project_id: str, memory_id: str, request: Request):
        data = await json_body(request)
        try:
            only(data, {'expected_revision'})
        except Invalid as exc:
            raise fail(exc) from None
        if 'expected_revision' not in data:
            raise HTTPException(400, {'code': 'invalid',
                                      'message': 'expected_revision is required'})
        await call(service.delete, project_id, memory_id,
                   expected_revision=data['expected_revision'])
        return Response(status_code=204)


def create_registrar(service: MemoryService):
    def registrar(app, dependencies):
        register(app, dependencies, service=service)
    return registrar
