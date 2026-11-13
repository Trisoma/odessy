"""Read-only workspace inspection and projections over existing authorization owners.

No routes are registered on import. Composition must supply the *session's* local
policy and a live permission/target check, never a root supplied by the HTTP caller.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import inspect
import os
from pathlib import Path, PureWindowsPath
import stat
from typing import Callable

from odessy.authority.redaction import redact_text
from odessy.authority.workspace_authority import WorkspaceAuthorityError
from odessy.runtime.hooks import load_hooks, screen_request
from odessy.runtime.types import ToolRequest

MAX_FILE_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_ENTRIES = 200


class InspectorError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class WorkspaceBinding:
    service_instance_id: str
    session_id: str
    workspace_id: str
    label: str
    policy: object
    # Must recheck session lifetime, local target and grants on every use.
    is_authorized: Callable[[], bool]
    local: bool
    proposed_changes: bool = False  # True only after the dispatch guard is wired.
    recorded_output: bool = True
    root_path: Path = field(init=False)
    root_identity: tuple = field(init=False)

    def __post_init__(self):
        root = self.policy.authority.workspace_root
        info = root.stat()
        object.__setattr__(self, 'root_path', root)
        object.__setattr__(self, 'root_identity', (info.st_dev, info.st_ino))

    def identity(self):
        return (self.service_instance_id, self.session_id, self.workspace_id,
                str(self.root_path), self.root_identity, repr(self.policy.identity()))


def _relative(path: str) -> str:
    if (not isinstance(path, str) or not path or len(path) > 2048 or
            any(ord(c) < 32 for c in path) or ':' in path or
            PureWindowsPath(path).anchor or path.startswith(('/', '~'))):
        raise InspectorError(400, 'A relative workspace path is required')
    parts = path.replace('\\', '/').split('/')
    if '..' in parts:
        raise InspectorError(403, 'Workspace path denied')
    return '/'.join(p for p in parts if p not in ('', '.')) or '.'


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class WorkspaceInspector:
    def __init__(self, resolve: Callable[[str], WorkspaceBinding]):
        self.resolve = resolve

    def binding(self, sid: str, previous: WorkspaceBinding | None = None):
        binding = self.resolve(sid)
        if binding is None:
            raise InspectorError(503, 'Workspace owner unavailable')
        if binding.session_id != sid:
            raise InspectorError(409, 'Workspace context changed')
        if binding.local is not True or binding.is_authorized() is not True:
            raise InspectorError(403, 'Workspace access denied')
        if previous is not None and binding.identity() != previous.identity():
            raise InspectorError(409, 'Workspace context changed')
        try:
            root = binding.policy.authority.workspace_root
            if not root.is_dir():
                raise InspectorError(503, 'Configured workspace unavailable')
            info = root.stat()
        except OSError:
            raise InspectorError(503, 'Configured workspace unavailable') from None
        if root != binding.root_path or (info.st_dev, info.st_ino) != binding.root_identity:
            raise InspectorError(409, 'Configured workspace changed')
        return binding

    async def _authorize(self, sid, path, operation):
        binding = self.binding(sid)
        path = _relative(path)
        request = ToolRequest(operation, {'path': path})
        try:
            decision = binding.policy.evaluate(request)
            decision = await screen_request(load_hooks(binding.policy.authority.workspace_root), request, decision)
        except (ValueError, PermissionError, OSError):
            raise InspectorError(403, 'Workspace policy denied access') from None
        # A read that hooks require to be confirmed cannot silently bypass the broker.
        if decision.action != 'allow':
            raise InspectorError(403, 'Workspace access requires authorization')
        self.binding(sid, binding)
        return binding, path, self._path(binding, path)

    @staticmethod
    def _path(binding, path):
        try:
            root = binding.policy.authority.workspace_root
            candidate = root
            # Reject every link/reparse component, including internal aliases. This
            # also prevents a harmless-looking link from aliasing a credential file.
            for part in path.split('/'):
                candidate = candidate / part
                info = candidate.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    raise InspectorError(403, 'Linked workspace paths are not inspectable')
            resolved = binding.policy.authority.resolve_path(path, must_exist=True)
            if resolved.sensitive:
                raise InspectorError(403, 'Workspace path denied')
            return resolved.resolved
        except FileNotFoundError:
            raise InspectorError(404, 'Workspace path not found') from None
        except (WorkspaceAuthorityError, OSError, ValueError):
            raise InspectorError(403, 'Workspace path denied') from None

    def descriptor(self, sid):
        binding = self.binding(sid)
        return {**self._identity(binding), 'label': binding.label,
                'capabilities': {'read_files': True,
                                 'proposed_changes': binding.proposed_changes,
                                 'recorded_output': binding.recorded_output}}

    @staticmethod
    def _identity(binding):
        return {'service_instance_id': binding.service_instance_id,
                'session_id': binding.session_id, 'workspace_id': binding.workspace_id}

    async def tree(self, sid, path='.'):
        binding, path, target = await self._authorize(sid, path, 'filesystem.list')
        if not target.is_dir():
            raise InspectorError(400, 'A directory is required')
        entries, truncated = [], False
        try:
            # Bound traversal work as well as returned entries. No recursive walk.
            with os.scandir(target) as iterator:
                for index, entry in enumerate(iterator):
                    if index >= MAX_ENTRIES:
                        truncated = True
                        break
                    relative = entry.name if path == '.' else path + '/' + entry.name
                    try:
                        child = self._path(binding, _relative(relative))
                        mode = child.stat().st_mode
                        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                            continue
                        # Each visible child passes policy, not just the directory.
                        request = ToolRequest('filesystem.read', {'path': relative})
                        decision = binding.policy.evaluate(request)
                        decision = await screen_request(load_hooks(binding.policy.authority.workspace_root), request, decision)
                        if decision.action != 'allow':
                            continue
                    except InspectorError:
                        continue
                    entries.append({'name': entry.name, 'path': relative,
                                    'kind': 'directory' if stat.S_ISDIR(mode) else 'file'})
        except (OSError, ValueError):
            raise InspectorError(403, 'Workspace directory unavailable') from None
        self.binding(sid, binding)
        return {**self._identity(binding), 'path': path,
                'entries': sorted(entries, key=lambda e: (e['kind'] != 'directory', e['name'].casefold())),
                'truncated': truncated}

    async def file(self, sid, path):
        binding, path, target = await self._authorize(sid, path, 'filesystem.read')
        data = self._read(binding, path, target)
        self.binding(sid, binding)
        return {**self._identity(binding), 'path': path, 'text': self._text(data),
                'sha256': _hash(data), 'truncated': False}

    def _read(self, binding, path, target=None):
        target = target or self._path(binding, path)
        try:
            before = target.stat()
            if not stat.S_ISREG(before.st_mode):
                raise InspectorError(415, 'A regular text file is required')
            # Nonblocking prevents a concurrent replacement with a FIFO from hanging.
            fd = os.open(target, os.O_RDONLY | getattr(os, 'O_BINARY', 0) |
                         getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as handle:
                opened = os.fstat(handle.fileno())
                if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
                    raise InspectorError(409, 'Workspace file changed')
                if opened.st_size > MAX_FILE_BYTES:
                    raise InspectorError(413, 'Workspace file exceeds 256 KiB')
                data = handle.read(MAX_FILE_BYTES + 1)
                after = os.fstat(handle.fileno())
            current = self._path(binding, path).stat()
            if (not os.path.samestat(opened, current) or
                    (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns)):
                raise InspectorError(409, 'Workspace file changed')
            if len(data) > MAX_FILE_BYTES:
                raise InspectorError(413, 'Workspace file exceeds 256 KiB')
            return data
        except OSError:
            raise InspectorError(403, 'Workspace file unavailable') from None

    @staticmethod
    def _text(data):
        try:
            text = data.decode('utf-8')
        except UnicodeError:
            raise InspectorError(415, 'File is not UTF-8 text') from None
        if any(ord(c) < 32 and c not in '\r\n\t' for c in text):
            raise InspectorError(415, 'Binary files are not inspectable')
        return text


def register(app, inspector: WorkspaceInspector):
    """Uses the application's existing bearer middleware; no extra auth surface."""
    from fastapi import HTTPException

    async def respond(method, *args):
        try:
            value = method(*args)
            return await value if inspect.isawaitable(value) else value
        except InspectorError as exc:
            raise HTTPException(exc.status, str(exc)) from None

    @app.get('/v1/sessions/{sid}/workspace')
    async def workspace(sid: str):
        return await respond(inspector.descriptor, sid)

    @app.get('/v1/sessions/{sid}/workspace/tree')
    async def tree(sid: str, path: str = '.'):
        return await respond(inspector.tree, sid, path)

    @app.get('/v1/sessions/{sid}/workspace/file')
    async def file(sid: str, path: str):
        return await respond(inspector.file, sid, path)


@dataclass(frozen=True)
class ReviewedWrite:
    """Dispatch guard, NOT an apply service or an authorization grant.

    Capture before the existing broker prompt; validate after the exact broker
    answer AND immediately in the existing bound-arguments dispatch validator.
    No production view may offer approval until that integration is installed.
    """
    identity: tuple
    session_id: str
    request_id: str
    path: str
    before_sha256: str
    after_sha256: str
    before_text: str
    after_text: str

    @classmethod
    async def capture(cls, inspector, sid, request_id, request):
        if request.name != 'filesystem.write' or set(request.arguments) != {'path', 'content'}:
            raise InspectorError(400, 'Only an exact filesystem write can be reviewed')
        content = request.arguments['content']
        if not request_id or not isinstance(content, str) or len(content.encode('utf-8')) > MAX_FILE_BYTES:
            raise InspectorError(400, 'Invalid bounded write proposal')
        binding = inspector.binding(sid)
        file = await inspector.file(sid, request.arguments['path'])
        return cls(binding.identity(), sid, request_id, file['path'], file['sha256'],
                   _hash(content.encode('utf-8')), file['text'], content)

    async def validate(self, inspector, sid, request_id, request):
        binding = inspector.binding(sid)
        if (binding.identity() != self.identity or sid != self.session_id or request_id != self.request_id or
                request.name != 'filesystem.write' or request.arguments != {'path': self.path, 'content': self.after_text}):
            raise InspectorError(409, 'Write proposal changed; new review required')
        current = await inspector.file(sid, self.path)
        if current['sha256'] != self.before_sha256:
            raise InspectorError(409, 'Workspace file changed; new review required')

    def validate_dispatch(self, inspector, request):
        """Synchronous check for the existing _BoundArguments dispatch validator.

        Existing plan.matches still checks policy, target and immutable arguments;
        this adds the missing content version without changing execution ownership.
        """
        binding = inspector.binding(self.session_id)
        if (binding.identity() != self.identity or request.name != 'filesystem.write' or
                request.arguments != {'path': self.path, 'content': self.after_text}):
            raise PermissionError('Reviewed write context changed')
        data = inspector._read(binding, self.path)
        if _hash(data) != self.before_sha256:
            raise PermissionError('Reviewed file changed; new approval required')

    def public(self):
        # Caller must apply the owning controller's credential redactor; if it
        # changes content, do not offer an exact-content diff approval.
        return {'session_id': self.session_id, 'request_id': self.request_id, 'path': self.path,
                'before_sha256': self.before_sha256, 'after_sha256': self.after_sha256,
                'before_text': self.before_text, 'after_text': self.after_text}


@dataclass(frozen=True)
class ContentBoundApproval:
    """Narrow decoration of the existing approval plan, not a new broker."""
    plan: object
    review: ReviewedWrite
    inspector: WorkspaceInspector

    def __getattr__(self, name):
        return getattr(self.plan, name)

    def public_dict(self):
        return {**self.plan.public_dict(), 'proposed_change': self.review.public()}

    def matches(self, request, decision, policy, *, execution_target):
        if not self.plan.matches(request, decision, policy, execution_target=execution_target):
            return False
        try:
            self.review.validate_dispatch(self.inspector, request)
            return True
        except (InspectorError, PermissionError):
            return False


async def bind_write_review(plan, request, inspector, sid, *, sanitize=redact_text):
    """Call immediately after existing build_action_approval, BEFORE prompting.

    The exact existing plan is retained. Its matches callback is already invoked
    after the broker reply and by _BoundArguments immediately before dispatch.
    Unsupported/nonlocal/new-file writes retain existing approvals without a
    fabricated diff; capability must not promise new-file review.
    """
    if request.name != 'filesystem.write':
        return plan
    try:
        review = await ReviewedWrite.capture(inspector, sid, plan.call_id, request)
    except InspectorError:
        return plan  # Keep the established approval path; never grant or execute.
    if sanitize(review.before_text) != review.before_text or sanitize(review.after_text) != review.after_text:
        return plan  # Redacted content cannot honestly be called an exact diff.
    return ContentBoundApproval(plan, review, inspector)


def recorded_outputs(result, *, session_id, turn_id, sanitize=redact_text):
    """Project settled public observations; progress titles are never stdout.

    Call at the existing result boundary with the controller's sanitizer and
    owning turn ID. No requests/arguments are exposed. Harness results without
    observations yield no output rather than invented terminal success.
    """
    outputs = []
    observations = getattr(result, 'tool_observations', ())
    if isinstance(result, dict):
        observations = result.get('tool_observations', ())
    for index, observation in enumerate(observations[:64]):
        if isinstance(observation, dict):
            request, value, call = observation.get('request', {}), observation.get('result', {}), observation.get('call_id')
            name = request.get('name')
        else:
            name, value, call = observation.request.name, observation.result, observation.call_id
        if name not in ('terminal.run', 'laptop.terminal.run') or not isinstance(value, dict):
            continue
        stdout, stderr = value.get('stdout', ''), value.get('stderr', '')
        if not isinstance(stdout, str) or not isinstance(stderr, str):
            continue
        # A refusal/error without output is useful; arbitrary tool metadata is not.
        state = {'ok': 'completed', 'failed': 'error'}.get(value.get('status'), value.get('status', 'unavailable'))
        if state not in ('completed', 'error', 'denied', 'cancelled', 'timeout', 'skipped'):
            state = 'completed' if type(value.get('exit_code')) is int else 'unavailable'
        truncated = bool(value.get('truncated', False) or value.get('stdout_truncated') or value.get('stderr_truncated'))
        bounded = []
        for text in (stdout, stderr):
            raw = sanitize(text).encode('utf-8')
            truncated |= len(raw) > MAX_OUTPUT_BYTES
            bounded.append(raw[:MAX_OUTPUT_BYTES].decode('utf-8', errors='ignore'))
        outputs.append({'session_id': session_id, 'turn_id': turn_id,
                        'call_id': call or f'{turn_id}:observation:{index}', 'tool_name': name,
                        'state': state, 'stdout': bounded[0], 'stderr': bounded[1],
                        'exit_code': value.get('exit_code') if type(value.get('exit_code')) is int else None,
                        'truncated': truncated})
    return outputs
