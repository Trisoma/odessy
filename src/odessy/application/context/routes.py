"""Read the last actually-prepared context for one conversation (B04/B10)."""
from __future__ import annotations

import asyncio

from .service import ContextService
from .wire import detail
from ..conversations.store import StorageError


def register(app, dependencies, *, service: ContextService) -> None:
    from fastapi import HTTPException

    @app.get('/v1/conversations/{conversation_id}/context')
    async def used_context(conversation_id: str):
        try:
            return await asyncio.to_thread(service.get_used, conversation_id)
        except StorageError as exc:
            raise HTTPException(status_code=exc.status, detail=detail(exc)) from None


def create_registrar(service: ContextService):
    def registrar(app, dependencies):
        register(app, dependencies, service=service)
    return registrar
