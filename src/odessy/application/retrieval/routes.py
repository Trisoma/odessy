"""HTTP routes for opt-in local document retrieval (B12)."""
import asyncio

from .service import MAX_LIMIT, MAX_QUERY, RetrievalService
from ..context.wire import detail, int_arg, only
from ..conversations.store import Invalid, StorageError


def register(app, dependencies, *, service: RetrievalService) -> None:
    from fastapi import HTTPException, Request
    from fastapi.responses import Response

    def fail(exc):
        payload = detail(exc) if isinstance(exc, StorageError) else {
            'code': 'denied' if getattr(exc, 'status', 500) in (401, 403) else 'invalid',
            'message': str(exc)}
        return HTTPException(status_code=getattr(exc, 'status', 500), detail=payload)

    async def json_body(request: Request):
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, {'code': 'invalid', 'message': 'Expected a JSON object'})
        if not isinstance(data, dict):
            raise HTTPException(400, {'code': 'invalid', 'message': 'Expected a JSON object'})
        return data

    def validate(function, *args):
        try:
            return function(*args)
        except Invalid as exc:
            raise fail(exc) from None

    @app.get('/v1/projects/{project_id}/documents')
    async def list_documents(project_id: str):
        try:
            documents = await asyncio.to_thread(service.documents, project_id)
            return {'version': 1, 'documents': documents}
        except StorageError as exc:
            raise fail(exc) from None

    @app.post('/v1/projects/{project_id}/documents', status_code=201)
    async def index_document(project_id: str, request: Request):
        data = await json_body(request)
        try:
            only(data, {'session_id', 'service_instance_id', 'path'})
        except Invalid as exc:
            raise fail(exc) from None
        for field in ('session_id', 'service_instance_id', 'path'):
            if not isinstance(data.get(field), str) or not data[field]:
                raise HTTPException(400, {'code': 'invalid', 'message': f'{field} is required'})
        try:
            return await service.ingest(
                project_id, session_id=data['session_id'],
                service_instance_id=data['service_instance_id'], path=data['path'])
        except (StorageError, Exception) as exc:  # inspector errors carry 4xx status
            if isinstance(exc, StorageError):
                raise fail(exc) from None
            status = getattr(exc, 'status', None)
            if status is None:
                raise
            raise fail(exc) from None

    @app.delete('/v1/projects/{project_id}/documents/{document_id}', status_code=204)
    async def remove_document(project_id: str, document_id: str, request: Request):
        data = await json_body(request)
        try:
            only(data, {'expected_revision'})
        except Invalid as exc:
            raise fail(exc) from None
        if 'expected_revision' not in data:
            raise HTTPException(400, {'code': 'invalid',
                                      'message': 'expected_revision is required'})
        try:
            await asyncio.to_thread(service.delete, project_id, document_id,
                                    expected_revision=data['expected_revision'])
        except StorageError as exc:
            raise fail(exc) from None
        return Response(status_code=204)

    @app.get('/v1/projects/{project_id}/retrieve')
    async def retrieve(project_id: str, session_id: str, query: str,
                       service_instance_id: str | None = None, limit: str | None = None):
        if not 1 <= len(query) <= MAX_QUERY:
            raise HTTPException(400, {'code': 'invalid',
                                      'message': f'query must be 1-{MAX_QUERY} characters'})
        try:
            limit_value = int_arg(limit, 'limit', 5, minimum=1, maximum=MAX_LIMIT)
            await service.verify_project(project_id, session_id=session_id,
                                         service_instance_id=service_instance_id)
            result = await asyncio.to_thread(service.retrieve, project_id, query,
                                             limit=limit_value)
            return {'version': 1, **result}
        except (StorageError, Exception) as exc:
            if isinstance(exc, StorageError):
                raise fail(exc) from None
            status = getattr(exc, 'status', None)
            if status is None:
                raise
            raise fail(exc) from None


def create_registrar(service: RetrievalService):
    def registrar(app, dependencies):
        register(app, dependencies, service=service)
    return registrar
