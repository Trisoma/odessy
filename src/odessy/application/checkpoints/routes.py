"""Frozen checkpoint routes; 07 supplies the bound service.

GET/POST /v1/sessions/{sid}/checkpoints
GET      /v1/sessions/{sid}/checkpoints/{checkpoint_id}/preview
POST     /v1/sessions/{sid}/checkpoints/{checkpoint_id}/restore
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from .service import CheckpointError, CheckpointService

_CODES = {400: 'invalid_request', 403: 'denied', 404: 'not_found', 409: 'conflict',
          413: 'too_large', 501: 'unsupported', 503: 'unavailable'}


def register(app: Any, dependencies: Any, *, service: CheckpointService) -> None:
    if not isinstance(service, CheckpointService):
        raise TypeError('service must be a CheckpointService')
    app.state.checkpoints = service

    def fail(error: CheckpointError):
        raise HTTPException(error.status,
                            detail={'code': _CODES.get(error.status, 'error'),
                                    'message': error.message})

    def invalid(message: str):
        raise HTTPException(400, detail={'code': 'invalid_request', 'message': message})

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            invalid('Expected a JSON object')
        if not isinstance(data, dict):
            invalid('Expected a JSON object')
        return data

    @app.get('/v1/sessions/{sid}/checkpoints')
    def list_checkpoints(sid: str):
        try:
            return service.list(sid)
        except CheckpointError as error:
            fail(error)

    @app.post('/v1/sessions/{sid}/checkpoints', status_code=201)
    async def create_checkpoint(sid: str, request: Request):
        data = await body(request)
        unknown = sorted(set(data) - {'label'})
        if unknown:
            invalid('Unknown checkpoint fields: ' + ', '.join(unknown))
        if 'label' not in data:
            invalid('label is required')
        try:
            return await service.run_async(service.create, sid, data['label'])
        except CheckpointError as error:
            fail(error)

    @app.get('/v1/sessions/{sid}/checkpoints/{checkpoint_id}/preview')
    def preview_checkpoint(sid: str, checkpoint_id: str):
        try:
            return service.preview(sid, checkpoint_id)
        except CheckpointError as error:
            fail(error)

    @app.post('/v1/sessions/{sid}/checkpoints/{checkpoint_id}/restore')
    async def restore_checkpoint(sid: str, checkpoint_id: str, request: Request):
        data = await body(request)
        unknown = sorted(set(data) - {'preview_hash', 'expected_revision'})
        if unknown:
            invalid('Unknown restore fields: ' + ', '.join(unknown))
        if 'preview_hash' not in data or 'expected_revision' not in data:
            invalid('preview_hash and expected_revision are required')
        try:
            return await service.run_async(service.restore,
                sid, checkpoint_id, preview_hash=data['preview_hash'],
                expected_revision=data['expected_revision'])
        except CheckpointError as error:
            fail(error)
