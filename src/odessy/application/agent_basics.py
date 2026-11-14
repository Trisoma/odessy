"""Optional agent-basics composition seam; no feature is enabled by default.

Workers implement independent route registrars. Only the application integrator
wires them into create_app; existing live-session semantics stay unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Protocol

AccessMode = Literal['ask', 'auto', 'full']
Continuation = Literal['continuable', 'history_only']
CONTRACT_VERSION = 1


@dataclass(frozen=True)
class BasicsCapabilities:
    conversations: bool = False
    custom_continuation: bool = False
    harness_continuation: bool = False
    access_modes: bool = False
    context: bool = False
    memory: bool = False
    retrieval: bool = False
    terminal: bool = False
    checkpoints: bool = False


@dataclass(frozen=True)
class BasicsDependencies:
    """Live lookups, not copied session state or a second execution owner."""
    service_instance_id: str
    find_session: Callable[[str], Any]


class BasicsRegistrar(Protocol):
    def __call__(self, app: Any, dependencies: BasicsDependencies) -> None: ...


@dataclass(frozen=True)
class AgentBasics:
    registrars: tuple[BasicsRegistrar, ...] = ()
    capabilities: BasicsCapabilities = BasicsCapabilities()

    def __post_init__(self) -> None:
        if not isinstance(self.registrars, tuple) or any(
            not callable(item) for item in self.registrars
        ):
            raise TypeError('Registrars must be an immutable tuple of callables')
        if not isinstance(self.capabilities, BasicsCapabilities):
            raise TypeError('Expected BasicsCapabilities')

    def mount(self, app: Any, dependencies: BasicsDependencies) -> None:
        for registrar in self.registrars:
            registrar(app, dependencies)


class ConversationStore(Protocol):
    """Synchronous, transactional store; HTTP owners bound/offload disk work."""
    def create(self, *, conversation_id: str, project_id: str, engine: str,
               profile: str, title: str) -> Mapping[str, Any]: ...
    def get(self, conversation_id: str) -> Mapping[str, Any]: ...
    def append(self, conversation_id: str, *, event_id: str, kind: str,
               payload: Mapping[str, Any], created_at: str) -> Mapping[str, Any]: ...
    def list(self, *, project_id: str | None = None, archived: bool = False,
             cursor: str | None = None, limit: int = 50) -> Mapping[str, Any]: ...
    def messages(self, conversation_id: str, *, before: int | None = None,
                 limit: int = 100) -> Mapping[str, Any]: ...
    def patch(self, conversation_id: str, *, expected_revision: int,
              title: str | None = None, pinned: bool | None = None,
              archived: bool | None = None) -> Mapping[str, Any]: ...
    def search(self, *, query: str, project_id: str | None = None,
               cursor: str | None = None, limit: int = 50) -> Mapping[str, Any]: ...
    def export(self, conversation_id: str) -> Mapping[str, Any]: ...
    def delete(self, conversation_id: str, *, expected_revision: int) -> None: ...
    def prepare_resume(self, conversation_id: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class ReviewInput:
    """Trusted dispatcher supplies this after screening; never parse from HTTP.

    fingerprint binds the existing ActionApprovalPlan, not redacted UI arguments.
    The dispatcher must revalidate both that plan and mode revision at dispatch.
    """
    session_id: str
    service_instance_id: str
    mode_revision: int
    operation: str
    fingerprint: str
    decision: Literal['allow', 'confirm', 'deny']
    rule: str | None
    risk: Literal['low', 'medium', 'high']
    scopes: tuple[str, ...]
    scope_authorized: bool
    current_binding: bool
    content_bound_review: bool = False


@dataclass(frozen=True)
class ReviewResult:
    outcome: Literal['approve', 'ask', 'deny']
    reason: str
    fingerprint: str
    mode_revision: int


@dataclass(frozen=True)
class PreparedContext:
    """Untrusted context data only; never stored authority or executable replay."""
    conversation_id: str
    revision: int
    turns: tuple[Any, ...] = ()
    summary: str = ''
    memory_ids: tuple[str, ...] = ()
    facts: tuple[Mapping[str, Any], ...] = ()
    citations: tuple[Mapping[str, Any], ...] = ()


class ContextProvider(Protocol):
    def prepare(self, conversation_id: str, *, query: str,
                max_chars: int = 24000) -> PreparedContext: ...
