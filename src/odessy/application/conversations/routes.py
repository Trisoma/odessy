"""Frozen Wave 1 HTTP routes for durable conversations.

Registration contract: ``register(app, dependencies, *, service)``. The integrator
(06) supplies the bound service and flips ``conversations`` in
``BasicsCapabilities`` only after mounting this registrar.

These routes own no runtime: browsing history, searching, exporting, renaming,
pinning, archiving and deleting never construct an engine or session. ``POST ...
/resume`` is routed through the injected continuation owner and returns 501 when
that owner is absent or the engine is Harness (history-only).
"""
import asyncio
from typing import Any

from .service import ConversationService, Unsupported
from .store import (Conflict, Invalid, NotFound, PayloadTooLarge, SchemaTooNew,
                    StorageError)

CAPABILITIES = {'conversations': True, 'custom_continuation': None,
                'harness_continuation': False}


def _detail(exc: StorageError) -> dict[str, str]:
    return {'code': exc.code, 'message': str(exc)}


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
        bounds = f'{minimum}-{maximum}' if maximum is not None else f'>= {minimum}'
        raise Invalid(f'{name} must be {bounds}')
    return parsed


def _bool_arg(value: Any, name: str, default: bool) -> bool:
    if value is None or value == '':
        return default
    if value in (True, 'true', 'True', '1', 1):
        return True
    if value in (False, 'false', 'False', '0', 0):
        return False
    raise Invalid(f'{name} must be true or false')


def register(app, dependencies, *, service: ConversationService) -> None:
    from fastapi import HTTPException, Request
    from fastapi.responses import JSONResponse, Response

    if not isinstance(service, ConversationService):
        raise TypeError('service must be a ConversationService')
    if not service.service_instance_id:
        service.service_instance_id = dependencies.service_instance_id

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

    def only(data: dict[str, Any], allowed: set[str]) -> None:
        unknown = set(data) - allowed
        if unknown:
            raise HTTPException(400, {'code': 'invalid',
                                      'message': f'Unknown fields: {", ".join(sorted(unknown))}'})

    async def call(function, *args, **kwargs):
        try:
            return await asyncio.to_thread(function, *args, **kwargs)
        except (Invalid, PayloadTooLarge, NotFound, Conflict, SchemaTooNew,
                Unsupported, StorageError) as exc:
            raise fail(exc) from None

    @app.get('/v1/projects')
    async def list_projects():
        try:
            return await asyncio.to_thread(service.projects)
        except StorageError as exc:
            raise fail(exc) from None

    @app.post('/v1/projects', status_code=201)
    async def create_project(request: Request):
        data = await json_body(request)
        only(data, {'label'})
        if 'label' not in data:
            raise HTTPException(400, {'code': 'invalid', 'message': 'label is required'})
        return await call(service.create_project, data['label'])

    @app.get('/v1/conversations')
    async def list_conversations(project_id: str | None = None, archived: str | None = None,
                                 cursor: str | None = None, limit: str | None = None):
        try:
            return await asyncio.to_thread(
                service.list, project_id=project_id,
                archived=_bool_arg(archived, 'archived', False), cursor=cursor,
                limit=_int_arg(limit, 'limit', 50, minimum=1, maximum=100))
        except StorageError as exc:
            raise fail(exc) from None

    @app.post('/v1/conversations', status_code=201)
    async def create_conversation(request: Request):
        data = await json_body(request)
        only(data, {'project_id', 'title'})
        if 'project_id' not in data:
            raise HTTPException(400, {'code': 'invalid', 'message': 'project_id is required'})
        return await call(service.create, project_id=data['project_id'],
                          title=data.get('title'))

    # Static path first: it must not be captured by the /{conversation_id} route.
    @app.get('/v1/conversations/search')
    async def search_conversations(query: str | None = None, project_id: str | None = None,
                                   cursor: str | None = None, limit: str | None = None):
        if query is None:
            raise HTTPException(400, {'code': 'invalid', 'message': 'query is required'})
        try:
            return await asyncio.to_thread(
                service.search, query=query, project_id=project_id, cursor=cursor,
                limit=_int_arg(limit, 'limit', 50, minimum=1, maximum=100))
        except StorageError as exc:
            raise fail(exc) from None

    @app.get('/v1/conversations/{conversation_id}')
    async def get_conversation(conversation_id: str):
        return await call(service.get, conversation_id)

    @app.patch('/v1/conversations/{conversation_id}')
    async def patch_conversation(conversation_id: str, request: Request):
        data = await json_body(request)
        only(data, {'expected_revision', 'title', 'pinned', 'archived'})
        if 'expected_revision' not in data:
            raise HTTPException(400, {'code': 'invalid',
                                      'message': 'expected_revision is required'})
        return await call(service.patch, conversation_id,
                          expected_revision=data['expected_revision'],
                          title=data.get('title'), pinned=data.get('pinned'),
                          archived=data.get('archived'))

    @app.get('/v1/conversations/{conversation_id}/messages')
    async def conversation_messages(conversation_id: str, before: str | None = None,
                                    limit: str | None = None):
        try:
            before_value = None if before in (None, '') else _int_arg(
                before, 'before', 0, minimum=0)
            return await asyncio.to_thread(
                service.messages, conversation_id, before=before_value,
                limit=_int_arg(limit, 'limit', 100, minimum=1, maximum=100))
        except StorageError as exc:
            raise fail(exc) from None

    @app.get('/v1/conversations/{conversation_id}/export')
    async def export_conversation(conversation_id: str):
        document = await call(service.export, conversation_id)
        return JSONResponse(content=document, headers={
            'Content-Disposition': f'attachment; filename="conversation-{conversation_id}.json"'})

    @app.delete('/v1/conversations/{conversation_id}', status_code=204)
    async def delete_conversation(conversation_id: str, request: Request):
        data = await json_body(request)
        only(data, {'expected_revision'})
        if 'expected_revision' not in data:
            raise HTTPException(400, {'code': 'invalid',
                                      'message': 'expected_revision is required'})
        await call(service.delete, conversation_id,
                   expected_revision=data['expected_revision'])
        close_owners = getattr(service, 'after_delete', None)
        if close_owners is not None:
            await close_owners(conversation_id)
        return Response(status_code=204)

    @app.post('/v1/conversations/{conversation_id}/resume')
    async def resume_conversation(conversation_id: str, request: Request):
        data = await json_body(request)
        only(data, {'expected_revision'})
        if 'expected_revision' not in data:
            raise HTTPException(400, {'code': 'invalid',
                                      'message': 'expected_revision is required'})
        try:
            return await service.resume(conversation_id,
                                        expected_revision=data['expected_revision'])
        except (Invalid, NotFound, Conflict, Unsupported, StorageError) as exc:
            raise fail(exc) from None


def create_registrar(service: ConversationService):
    """Bind one service to the immutable ``AgentBasics`` registrar signature."""
    def registrar(app, dependencies):
        register(app, dependencies, service=service)
    return registrar
