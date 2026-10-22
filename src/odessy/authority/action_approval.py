"""Deterministic approval snapshots. Public presentation never defines authority."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from pathlib import PurePath
from typing import Any

from odessy.authority.redaction import redact_text
from odessy.authority.workspace_authority import WorkspaceAuthorityError
from odessy.tools.external_tools import external_tool


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError('Approval mappings require string keys')
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(item) for item in value)
    if isinstance(value, PurePath):
        # Host paths are lexical identities; never resolve a laptop path on Jetson.
        return str(value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError(f'Unsupported approval identity: {type(value).__name__}')


def _json(value: Any) -> str:
    return json.dumps(_plain(value), sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _display(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ('[REDACTED]' if re.search(r'password|secret|token|credential|api[_-]?key', key, re.I) else _display(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_display(item) for item in value]
    if isinstance(value, str):
        value = redact_text(value)
        # Escape controls/bidi formatting for display without changing bound bytes.
        return ''.join(f'\\u{ord(char):04x}' if unicodedata.category(char) in {'Cc', 'Cf'} and char not in '\n\r\t' else {'\n': '\\n', '\r': '\\r', '\t': '\\t'}.get(char, char) for char in value)
    return value


@dataclass(frozen=True, slots=True)
class ActionApprovalPlan:
    call_id: str
    risk: str
    allowed_channels: tuple[str, ...]
    scopes: tuple[str, ...]
    authoritative_prompt: str
    fingerprint: str
    _canonical: str
    _public: str

    def public_dict(self) -> dict[str, Any]:
        return json.loads(self._public)

    def matches(self, request, decision, policy, *, execution_target) -> bool:
        try:
            other = build_action_approval(request, decision, policy, call_id=self.call_id, execution_target=execution_target)
            return self._canonical == other._canonical
        except (ValueError, TypeError, WorkspaceAuthorityError):
            return False


def build_action_approval(request, decision, policy, *, call_id: str, execution_target) -> ActionApprovalPlan:
    if not isinstance(request.name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', request.name):
        raise ValueError('Invalid approval tool name')
    if not isinstance(call_id, str) or not call_id:
        raise ValueError('Approval requires a call ID')
    target = _plain(execution_target)
    if not isinstance(target, (str, dict)) or not target:
        raise ValueError('Approval requires a stable execution target')
    host = target if isinstance(target, str) else target.get('host', target.get('name', 'configured target'))
    if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9_.:/@ -]{1,160}', host):
        raise ValueError('Invalid approval host')
    args = _plain(request.arguments)
    external = external_tool(request.name)
    external_binding = None if external is None else _plain(external)
    paths = {}
    # Only local workspace tools share this authority. Laptop execution and
    # third-party server paths must not be resolved against the controller FS.
    if not request.name.startswith('laptop.') and external is None:
        path_keys = {'cwd', 'path', 'file'}
        selected = {key: value for key, value in args.items() if key in path_keys and isinstance(value, str)}
        if request.name == 'terminal.run':
            selected.setdefault('cwd', '.')
        for key, value in selected.items():
            resolved = policy.authority.resolve_path(value, must_exist=False)
            paths[key] = str(resolved.resolved)
    policy_data = {'policy': _plain(policy), 'external_tool': external_binding}
    policy_binding = _hash(_json(policy_data))
    scopes = []
    if decision.action != 'deny':
        if decision.requires_confirmation and (not decision.preapproved or decision.risk == 'high'):
            scopes.append('authorization')
        if 'sandbox_permissions' in args:
            scopes.append('escalation')
    channels = () if decision.action == 'deny' else (('terminal', 'authenticated_ui') if decision.risk == 'high' or 'sandbox_permissions' in args else ('voice', 'terminal', 'authenticated_ui'))
    canonical = _json({'call_id': call_id, 'operation': request.name, 'arguments': args, 'resolved_paths': paths, 'workspace': str(policy.authority.workspace_root), 'host': host, 'target_identity': target, 'decision': _plain(decision), 'policy': policy_data, 'scopes': scopes, 'allowed_channels': channels})
    fingerprint = _hash(canonical)
    prompt = f'Approve {request.name} on {host} for this action only. Risk: {decision.risk}.'
    if request.name in {'terminal.run', 'laptop.terminal.run'} and isinstance(args.get('command'), str):
        prompt += f' Exact command: {_display(args["command"])}.'
    display_paths = paths or {key: value for key, value in args.items() if key in {'cwd', 'path', 'file'} and isinstance(value, str)}
    for key, value in sorted(display_paths.items()):
        prompt += f' {key}: {_display(value)}.'
    prompt += ' Scopes: ' + (', '.join(scopes) if scopes else 'none') + '.'
    if 'escalation' in scopes:
        prompt += f' Includes sandbox escalation to {_display(args.get("sandbox_permissions"))}.'
    public = {'authoritative_prompt': prompt, 'risk': decision.risk, 'allowed_channels': list(channels), 'scopes': scopes, 'call_id': call_id, 'operation': request.name, 'host': host, 'arguments': _display(args), 'resolved_paths': _display(paths), 'target_identity': _display(target), 'policy_binding': policy_binding, 'fingerprint': fingerprint}
    if isinstance(args.get('justification'), str):
        public['model_reason'] = _display(args['justification'])
    return ActionApprovalPlan(call_id, decision.risk, channels, tuple(scopes), prompt, fingerprint, canonical, _json(public))
