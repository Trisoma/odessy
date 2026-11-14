"""Build the Wave 2 context/memory/retrieval services and route registrars.

07 is the only caller that mounts these. All authorization inputs are callbacks
supplied by ``PersistentComposition``; no absolute root, project id or request field
is trusted on its own.
"""
from __future__ import annotations

from dataclasses import dataclass

from .routes import create_registrar as context_registrar
from .schema import migrate_extensions
from .service import ContextService
from ..memory.routes import create_registrar as memory_registrar
from ..memory.service import MemoryService
from ..retrieval.routes import create_registrar as retrieval_registrar
from ..retrieval.service import RetrievalService


@dataclass(frozen=True)
class Wave2Services:
    context: ContextService
    memory: MemoryService
    retrieval: RetrievalService
    registrars: tuple
    capability_flags: dict


def build_wave2_services(*, store, resolve_conversation_owner, resolve_context_workspace,
                         resolve_project_workspace, read_context_file,
                         session_conversation=None, service_instance_id=''):
    """Migrate extensions once, then construct the three services.

    ``store`` is prompt 01's ConversationStore. ``session_conversation`` maps a live
    session id to its durable conversation id (07 supplies it); retrieval needs it to
    use ``read_context_file(conversation_id, path)``.
    """
    with store.connection() as db:
        migrate_extensions(db)
    memory = MemoryService(store)
    retrieval = RetrievalService(store, project_workspace=resolve_project_workspace,
                                 read_file=read_context_file,
                                 session_conversation=session_conversation)
    context = ContextService(store, memory=memory, retrieval=retrieval)
    return Wave2Services(
        context=context, memory=memory, retrieval=retrieval,
        registrars=(context_registrar(context), memory_registrar(memory),
                    retrieval_registrar(retrieval)),
        capability_flags={'context': True, 'memory': True, 'retrieval': True})
