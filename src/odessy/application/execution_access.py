"""Execution credentials issued to a coordinator that the local user accepted.

A paired coordinator needs the laptop's *tool* surface, not this application's
API, so the two authorities are separate on purpose:

* this module issues an opaque secret scoped to one workspace and one pairing;
* nothing here authorizes settings, pairing approval, sessions or management;
* only the tool endpoint (the Node MCP server) checks the secret, through the
  ``ODESSY_MCP_TOKEN`` it is started with.

The secret is never persisted: a restart voids every grant, and the coordinator
must pair again. That is deliberate — an unreviewed artifact on disk is worse
than a repeated tap.
"""
from __future__ import annotations

import hmac
import time

#: Concurrent grants are bounded so a stream of requests cannot grow state.
MAX_GRANTS = 32
#: A grant outlives one working day and no longer.
LIFETIME_SECONDS = 12 * 60 * 60

#: credential -> {'pairing': str, 'workspace': str, 'expires': float}
_grants: dict[str, dict] = {}
#: The secret the hosted tool server was started with for this session.
_session: dict[str, str | None] = {'token': None, 'workspace': None}


def configure(token, workspace) -> None:
    """Adopt the secret the LAN-hosted tool server was started with.

    One secret per hosting session: the server validates it itself, so a second
    secret would need a way to push it into a running Node process. Restarting the
    host rotates it, which is also how a grant is withdrawn.
    """
    if not isinstance(token, str) or not token:
        raise ValueError('an execution secret is required')
    _session['token'] = token
    _session['workspace'] = str(workspace) if workspace else ''


def configured() -> bool:
    return _session['token'] is not None


def reset() -> None:
    """Forget the session secret and every grant (shutdown, and tests)."""
    _session.update(token=None, workspace=None)
    _grants.clear()


def issue(pairing_id, workspace, *, now=None) -> str:
    """Mint one credential for *pairing_id* limited to *workspace*."""
    if not isinstance(pairing_id, str) or not pairing_id:
        raise ValueError('a pairing identity is required')
    if not isinstance(workspace, str) or not workspace:
        raise ValueError('a reviewed workspace is required')
    token = _session['token']
    if token is None:
        raise ValueError('LAN tool hosting is not enabled on this computer')
    if workspace != _session['workspace']:
        raise ValueError('the reviewed workspace does not match the hosted tool surface')
    moment = time.time() if now is None else now
    _sweep(moment)
    if len(_grants) >= MAX_GRANTS:
        raise ValueError('too many execution grants are active')
    _grants[token] = {'pairing': pairing_id, 'workspace': workspace,
                      'expires': moment + LIFETIME_SECONDS}
    return token


def validate(credential, *, workspace=None, now=None) -> bool:
    """True when *credential* is live and, if given, scoped to *workspace*."""
    token = _session['token']
    if token is None or not isinstance(credential, str):
        return False
    moment = time.time() if now is None else now
    grant = _grants.get(credential)
    if grant is None or grant['expires'] <= moment or not hmac.compare_digest(credential, token):
        return False
    return workspace is None or grant['workspace'] == workspace


def grant_for(credential) -> dict | None:
    """The recorded scope, for a caller that has already validated the secret."""
    return dict(_grants[credential]) if credential in _grants else None


def revoke(pairing_id) -> int:
    """Drop every grant issued for *pairing_id*; returns how many were removed."""
    doomed = [key for key, value in _grants.items() if value['pairing'] == pairing_id]
    for key in doomed:
        del _grants[key]
    return len(doomed)


def _sweep(now) -> None:
    for key in [key for key, value in _grants.items() if value['expires'] <= now]:
        del _grants[key]
