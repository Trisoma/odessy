"""Durable conversations and transcript history (Wave 1, prompt 01).

Public seam used by the integrator (06)::

    from odessy.application.conversations import (
        build_service, create_registrar, ConversationRecorder, settle_after_restart)

    service = build_service(continuation=resume_legacy_session, settings=current_route)
    basics = AgentBasics(
        registrars=(create_registrar(service),),
        capabilities=BasicsCapabilities(conversations=True,
                                        custom_continuation=service.continuation_kind('legacy') == 'continuable'))

Nothing here is mounted or enabled by default; no model, process, capture or
download is started by importing or mounting it.
"""
from __future__ import annotations

from .lifecycle import (ConversationLog, ConversationRecorder, adopt_live_session,
                        settle_after_restart)
from .routes import create_registrar, register
from .service import ConversationService, Unsupported, build_service
from .store import (Conflict, ConversationStore, CorruptDatabase, Invalid, NotFound,
                    PayloadTooLarge, SchemaTooNew, StorageError, default_store_path)

__all__ = [
    'ConversationStore', 'ConversationService', 'build_service', 'create_registrar',
    'register', 'ConversationRecorder', 'ConversationLog', 'adopt_live_session',
    'settle_after_restart', 'default_store_path', 'StorageError', 'NotFound', 'Conflict',
    'Invalid', 'PayloadTooLarge', 'SchemaTooNew', 'CorruptDatabase', 'Unsupported',
]
