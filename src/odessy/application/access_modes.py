"""Server-side session access modes and the real deterministic policy reviewer.

Modes are authority, not decoration: the trusted dispatcher calls :meth:`AccessModeService.review`
once the existing policy, hooks and exact ``ActionApprovalPlan`` have produced a decision, and
:meth:`AccessModeService.validate` at the last dispatch barrier before any effect.

* ``ask``  — every authorizable side effect asks; an ordinary policy *allow* (a harmless read)
  is not turned into a prompt.
* ``auto`` — ``policy-v1`` may approve one bounded class of ordinary workspace writes. Everything
  else asks: terminal commands, hooks, delegation, escalation, unknown or changed plans,
  reviewer errors, high risk.
* ``full`` — suppresses routine *authorization-only* confirmations inside the current scope.
  Escalation, hook confirmations and hard denials are never suppressed.

Confinement, sensitive-path denials, sensitive-path policy and independent permissions
(paired credentials in ``execution_access``) are untouched by every mode.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from .agent_basics import AccessMode, BasicsDependencies, ReviewInput, ReviewResult

#: Stable reviewer identity. A different policy needs a new identifier, not a silent change.
REVIEWER = 'policy-v1'

SUPPORTED_MODES: tuple[AccessMode, ...] = ('ask', 'auto', 'full')
NO_MODES: tuple[AccessMode, ...] = ()

#: The exact, single class of action ``auto`` may approve without asking.
AUTO_OPERATION = 'filesystem.write'
AUTO_RULE = 'workspace_write'
AUTO_RISK = 'medium'
AUTO_SCOPES: tuple[str, ...] = ('authorization',)

_MODES = frozenset(SUPPORTED_MODES)


class AccessModeError(Exception):
    """Transport-neutral failure; routes translate ``code`` to an HTTP status."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ModeSnapshot:
    session_id: str
    service_instance_id: str
    mode: AccessMode
    revision: int
    supported_modes: tuple[AccessMode, ...]
    reviewer: str
    scope: Mapping[str, str] | None
    effective: bool

    def public(self) -> dict[str, Any]:
        return {
            'version': 1,
            'session_id': self.session_id,
            'service_instance_id': self.service_instance_id,
            'mode': self.mode,
            'revision': self.revision,
            'supported_modes': list(self.supported_modes),
            'reviewer': self.reviewer,
            'scope': None if self.scope is None else {
                'workspace_id': self.scope['workspace_id'],
                'target_id': self.scope['target_id'],
            },
            'effective': self.effective,
        }


@dataclass(frozen=True)
class ReviewRecord:
    """In-memory audit of one decision. Bounded; never persisted with raw arguments."""

    session_id: str
    service_instance_id: str
    operation: str
    rule: str | None
    risk: str
    scopes: tuple[str, ...]
    fingerprint: str
    revision: int
    outcome: str
    reason: str


@dataclass
class _State:
    mode: AccessMode = 'ask'
    revision: int = 0
    #: Bumped on every mode change and on close; a grant from an older generation is dead.
    generation: int = 0
    scope: Mapping[str, str] | None = None


class AccessModeService:
    """Per-session mode state plus fail-closed review/validation.

    ``resolve_scope(session_id, controller)`` is a trusted callback supplied by the integrator:
    it must return the live authorized ``{'workspace_id', 'target_id'}`` for the session or None.
    Without it no mode is reported effective, because no scope can be proven.
    """

    def __init__(
        self,
        dependencies: BasicsDependencies,
        *,
        resolve_scope: Callable[[str, Any], Mapping[str, Any] | None] | None = None,
        clock: Callable[[], float] | None = None,
        grant_ttl: float = 900.0,
        max_records: int = 256,
    ) -> None:
        if not isinstance(dependencies, BasicsDependencies):
            raise TypeError('Expected BasicsDependencies')
        if grant_ttl <= 0:
            raise ValueError('grant_ttl must be positive')
        self._dependencies = dependencies
        self._resolve_scope = resolve_scope
        self._clock = clock or time.monotonic
        self._grant_ttl = float(grant_ttl)
        self._lock = threading.Lock()
        self._states: dict[str, _State] = {}
        #: (session, fingerprint, generation, revision) -> expiry; validated once, then consumed.
        self._grants: dict[tuple[str, str, int, int], float] = {}
        self._records: deque[ReviewRecord] = deque(maxlen=max_records)

    # ------------------------------------------------------------------ lookups
    def _controller(self, session_id: Any) -> Any:
        if not isinstance(session_id, str) or not session_id:
            raise AccessModeError(404, 'Session not found')
        try:
            controller = self._dependencies.find_session(session_id)
        except AccessModeError:
            raise
        except Exception as error:  # FastAPI 404, KeyError, or a closed owner
            raise AccessModeError(404, 'Session not found') from error
        if controller is None:
            raise AccessModeError(404, 'Session not found')
        return controller

    @staticmethod
    def _modes(controller: Any) -> tuple[AccessMode, ...]:
        """Only engines with a real adapter advertise modes. No silent mapping."""
        return SUPPORTED_MODES if getattr(controller, 'engine', None) == 'legacy' else NO_MODES

    def _scope(self, session_id: str, controller: Any) -> Mapping[str, str] | None:
        if self._resolve_scope is None:
            return None
        try:
            value = self._resolve_scope(session_id, controller)
        except Exception:
            return None
        if not isinstance(value, Mapping):
            return None
        workspace, target = value.get('workspace_id'), value.get('target_id')
        if not isinstance(workspace, str) or not workspace:
            return None
        if not isinstance(target, str) or not target:
            return None
        return {'workspace_id': workspace, 'target_id': target}

    def _state(self, session_id: str) -> _State:
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                state = _State()
                self._states[session_id] = state
            return state

    # ------------------------------------------------------------------ wire
    def snapshot(self, session_id: str) -> dict[str, Any]:
        controller = self._controller(session_id)
        state = self._state(session_id)
        modes = self._modes(controller)
        live = self._scope(session_id, controller) if modes else None
        with self._lock:
            mode, revision, bound = state.mode, state.revision, state.scope
        # ``ask`` needs no bound grant; a stored scope only exists once a mode was set.
        effective = bool(modes) and live is not None and (bound is None or bound == live)
        return ModeSnapshot(
            session_id=session_id,
            service_instance_id=self._dependencies.service_instance_id,
            mode=mode,
            revision=revision,
            supported_modes=modes,
            reviewer=REVIEWER,
            scope=live,
            effective=effective,
        ).public()

    def set_mode(
        self,
        session_id: str,
        *,
        mode: AccessMode,
        expected_revision: int,
        service_instance_id: str,
    ) -> dict[str, Any]:
        if not isinstance(mode, str) or mode not in _MODES:
            raise AccessModeError(400, 'mode must be one of ask, auto, full')
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) \
                or expected_revision < 0:
            raise AccessModeError(400, 'expected_revision must be a non-negative integer')
        if not isinstance(service_instance_id, str) or not service_instance_id:
            raise AccessModeError(400, 'service_instance_id is required')
        controller = self._controller(session_id)
        modes = self._modes(controller)
        if not modes:
            raise AccessModeError(
                501, 'This engine has no server-side access-mode adapter; it stays ask-only')
        if service_instance_id != self._dependencies.service_instance_id:
            raise AccessModeError(409, 'Service identity changed; reload the session')
        live = self._scope(session_id, controller)
        if live is None:
            raise AccessModeError(409, 'No live authorized scope for this session')
        state = self._state(session_id)
        with self._lock:
            if expected_revision != state.revision:
                raise AccessModeError(
                    409, f'Access mode revision changed (expected {expected_revision}, '
                         f'current {state.revision})')
            state.mode = mode
            state.revision += 1
            state.generation += 1
            state.scope = dict(live)
            # Any pending decision prepared under the previous mode is dead.
            self._grants = {key: expiry for key, expiry in self._grants.items()
                            if key[0] != session_id}
        return self.snapshot(session_id)

    # ------------------------------------------------------------------ reviewer
    def review(self, review_input: ReviewInput) -> ReviewResult:
        if not isinstance(review_input, ReviewInput):
            raise TypeError('review() requires a trusted ReviewInput')
        data = review_input
        try:
            controller = self._controller(data.session_id)
        except AccessModeError:
            return self._record(data, 'ask', 'no live session for this decision; confirm manually')
        state = self._state(data.session_id)
        modes = self._modes(controller)

        if data.decision == 'deny':
            # Hard denials are never converted into an approval by any mode.
            return self._record(data, 'deny', 'policy denied this action')
        if data.decision == 'allow':
            return self._record(data, 'approve',
                                'policy allowed this action without confirmation')
        if data.decision != 'confirm':
            return self._record(data, 'ask', 'unrecognized policy decision; confirm manually')
        if not modes:
            return self._record(data, 'ask',
                                'engine has no server-side access-mode adapter; confirm manually')
        if data.service_instance_id != self._dependencies.service_instance_id:
            return self._record(data, 'ask', 'service identity changed; confirm manually')

        with self._lock:
            mode, revision, bound = state.mode, state.revision, state.scope
        if data.mode_revision != revision:
            return self._record(
                data, 'ask',
                f'decision prepared for access-mode revision {data.mode_revision}, '
                f'current is {revision}')
        live = self._scope(data.session_id, controller)
        if live is None or (bound is not None and bound != live):
            return self._record(data, 'ask', 'no current authorized scope for this session')
        if not data.scope_authorized:
            return self._record(data, 'ask', 'session is outside its authorized scope')
        if not data.current_binding:
            return self._record(data, 'ask',
                                'action arguments or execution target changed since screening')

        rule = data.rule if isinstance(data.rule, str) else None
        if mode == 'ask':
            return self._record(data, 'ask', 'ask mode requires confirmation for this action')
        if mode == 'auto':
            return self._review_auto(data, rule)
        return self._review_full(data, rule)

    def _review_auto(self, data: ReviewInput, rule: str | None) -> ReviewResult:
        if data.operation != AUTO_OPERATION:
            return self._record(data, 'ask',
                                f'auto mode does not cover {data.operation}; confirm manually')
        if rule != AUTO_RULE:
            return self._record(
                data, 'ask',
                f'auto mode only covers the {AUTO_RULE} rule, not {rule or "no rule"}')
        if data.risk != AUTO_RISK:
            return self._record(data, 'ask',
                                f'auto mode only covers medium risk, not {data.risk}')
        if tuple(data.scopes) != AUTO_SCOPES:
            return self._record(
                data, 'ask', 'auto mode only covers an authorization-only confirmation')
        if not data.content_bound_review:
            return self._record(
                data, 'ask',
                'auto mode requires a content-bound diff review of the exact change')
        return self._record(
            data, 'approve',
            'policy-v1: bounded workspace_write approved within the current scope and revision',
            grant=True)

    def _review_full(self, data: ReviewInput, rule: str | None) -> ReviewResult:
        if 'escalation' in data.scopes:
            return self._record(data, 'ask',
                                'full mode never suppresses a sandbox escalation')
        if rule is not None and rule.startswith('hook:'):
            return self._record(data, 'ask',
                                'full mode never suppresses a hook confirmation')
        if set(data.scopes) - {'authorization'}:
            return self._record(
                data, 'ask',
                'full mode only suppresses authorization confirmations, not '
                + ', '.join(sorted(set(data.scopes) - {'authorization'})))
        return self._record(
            data, 'approve',
            'policy-v1: authorization-only action approved inside the current scope and revision',
            grant=True)

    def _record(self, data: ReviewInput, outcome: str, reason: str,
                *, grant: bool = False) -> ReviewResult:
        if outcome not in ('approve', 'ask', 'deny'):
            raise ValueError('Invalid review outcome')
        if grant:
            try:
                controller = self._controller(data.session_id)
            except AccessModeError:
                controller = None
            state = self._state(data.session_id) if controller is not None else None
            if state is not None and self._modes(controller):
                key = (data.session_id, data.fingerprint, state.generation, state.revision)
                with self._lock:
                    self._grants[key] = self._clock() + self._grant_ttl
        result = ReviewResult(
            outcome=outcome,  # type: ignore[arg-type]
            reason=reason,
            fingerprint=data.fingerprint,
            mode_revision=data.mode_revision,
        )
        self._audit(ReviewRecord(
            session_id=data.session_id,
            service_instance_id=data.service_instance_id,
            operation=data.operation,
            rule=data.rule,
            risk=data.risk,
            scopes=tuple(data.scopes),
            fingerprint=data.fingerprint,
            revision=data.mode_revision,
            outcome=outcome,
            reason=reason,
        ))
        return result

    def _audit(self, record: ReviewRecord) -> None:
        with self._lock:
            self._records.append(record)

    def records(self) -> tuple[ReviewRecord, ...]:
        with self._lock:
            return tuple(self._records)

    # ------------------------------------------------------------------ barrier
    def validate(self, result: ReviewResult, review_input: ReviewInput) -> bool:
        """Last dispatch barrier. A passing grant is consumed: it authorizes one dispatch."""
        if not isinstance(result, ReviewResult) or not isinstance(review_input, ReviewInput):
            return False
        if result.outcome != 'approve':
            return False
        if result.fingerprint != review_input.fingerprint:
            return False
        try:
            controller = self._controller(review_input.session_id)
        except AccessModeError:
            return False
        with self._lock:
            state = self._states.get(review_input.session_id)
            if state is None:
                return False
            revision, generation, bound = state.revision, state.generation, state.scope
        if result.mode_revision != revision or review_input.mode_revision != revision:
            return False
        if review_input.service_instance_id != self._dependencies.service_instance_id:
            return False
        if not self._modes(controller):
            return False
        live = self._scope(review_input.session_id, controller)
        if live is None or (bound is not None and bound != live):
            return False
        if not review_input.scope_authorized or not review_input.current_binding:
            return False
        key = (review_input.session_id, result.fingerprint, generation, revision)
        with self._lock:
            expiry = self._grants.pop(key, None)
        if expiry is None:
            return False
        return self._clock() <= expiry

    # ------------------------------------------------------------------ lifecycle
    def close(self, session_id: str) -> None:
        """Session ended or restarted: its mode, revision and grants do not survive."""
        if not isinstance(session_id, str) or not session_id:
            return
        with self._lock:
            state = self._states.pop(session_id, None)
            if state is not None:
                state.generation += 1
            self._grants = {key: expiry for key, expiry in self._grants.items()
                            if key[0] != session_id}

    def close_all(self) -> None:
        with self._lock:
            self._states.clear()
            self._grants.clear()

    def sessions(self) -> Iterable[str]:
        with self._lock:
            return tuple(self._states)
