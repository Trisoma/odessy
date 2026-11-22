"""Role profile matching: turn a role name into candidate participants.

This module is a *scheduling preference* layer only. It never grants authority,
never checks capabilities or allowed modes, and never executes anything. A
caller (the future planner) must still apply the ordinary collaboration rules,
participant capabilities, allowed modes and the high-risk approval gate before
any work is dispatched. "The model proposes; Odessy decides" still applies.
"""
from __future__ import annotations

from odessy.collaboration.config import CollaborationConfig


def declared_roles(config: CollaborationConfig) -> tuple[str, ...]:
    """All role names this configuration knows about, sorted for stable display.

    A role is known if it is explicitly bound in ``settings.roles`` or declared
    as a capability tag by at least one participant.
    """
    names = set(config.settings.roles)
    for profile in config.participants.values():
        names.update(profile.roles)
    return tuple(sorted(names))


def bindings_for(config: CollaborationConfig, role: str) -> tuple[str, ...]:
    """Explicit user bindings for ``role``, in configured order.

    Names that do not correspond to a configured participant are dropped so a
    stale binding can never select a phantom agent. This is still only a
    preference: the caller owns all authority decisions.
    """
    targets = config.settings.roles.get(role, ())
    configured = config.participants
    return tuple(name for name in targets if name in configured)


def tagged_for(config: CollaborationConfig, role: str) -> tuple[str, ...]:
    """Participants that advertise ``role`` as a capability tag, in name order.

    Used as a deterministic fallback when no explicit binding exists.
    """
    return tuple(sorted(
        name for name, profile in config.participants.items()
        if role in profile.roles
    ))


def resolve_role(config: CollaborationConfig, role: str) -> tuple[str, ...]:
    """Return candidate participant names for ``role``, in priority order.

    Explicit user bindings take precedence; when there are none, participants
    whose ``roles`` tag contains the role are used. Duplicates are removed and
    the result contains only configured participants.

    The return value is a *suggestion*. It deliberately says nothing about
    whether a candidate may consult, delegate, write files, or run commands —
    capabilities, allowed modes, rules and approval are checked by the caller.
    """
    if not isinstance(role, str) or not role:
        return ()
    candidates = bindings_for(config, role) or tagged_for(config, role)
    seen = set()
    ordered = []
    for name in candidates:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return tuple(ordered)
