"""Audit log of every tool call: what was asked, decided, and what happened.

One JSON object per line, appended to ``~/.local/state/odessy/external-audit.jsonl``
(``ODESSY_AUDIT_FILE`` sets another path; an empty value turns the log off). Two kinds
of record, written in order:

* ``decision``: the policy decision for a proposed call, the rule that made it, and
  whether the user confirmed it. A denied or declined call has only this record.
* ``call``: an executed call's outcome (``ok``, ``error``, ``timeout``, ``failed``)
  and duration.
* ``approval``: a question put to a person on the desktop or by voice: its fingerprint,
  the answer, the channel that gave it, or why it ended unanswered.

Arguments are recorded as their names and the SHA-256 of each value, never the values:
the log shows which calls repeated the same input without holding file contents or
secrets. A hash of a short or guessable value can be reversed by guessing, so the log
is still private to the user. Odessy's own tools have ``server`` set to ``builtin``.
The file keeps its original name, from when it held third-party calls only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from odessy.runtime.types import PolicyDecision
    from odessy.tools.external_tools import ServerConfig

AUDIT_FILE_ENV = "ODESSY_AUDIT_FILE"


class AuditLogError(OSError):
    """The audit log cannot be written where it is configured."""


def default_audit_file() -> Path:
    return Path.home() / ".local" / "state" / "odessy" / "external-audit.jsonl"


def audit_file(environ: Mapping[str, str] | None = None) -> Path | None:
    """The configured audit file, or None when the log is turned off."""

    source = os.environ if environ is None else environ
    if AUDIT_FILE_ENV in source:
        explicit = source[AUDIT_FILE_ENV].strip()
        return Path(explicit).expanduser() if explicit else None
    return default_audit_file()


def hash_arguments(arguments: Mapping[str, Any]) -> dict[str, str]:
    """Each argument's name and the SHA-256 of its JSON value."""

    return {
        str(name): hashlib.sha256(
            json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()
        for name, value in sorted(arguments.items(), key=lambda item: str(item[0]))
    }


class ExternalAuditLog:
    """Appends audit records. A failed write is reported once and does not stop the run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._warned = False

    @classmethod
    def open(cls, environ: Mapping[str, str] | None = None) -> ExternalAuditLog | None:
        """The configured log with its directory created, or None when turned off.

        Raises ``AuditLogError`` when the directory cannot be created, so a log the
        user expects is never silently missing from the start.
        """

        path = audit_file(environ)
        if path is None:
            return None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise AuditLogError(
                f"cannot create the audit log directory {path.parent}: {error}. "
                f"Set {AUDIT_FILE_ENV} to another path, or to an empty value to turn the log off."
            ) from error
        return cls(path)

    def decision(
        self,
        *,
        server: ServerConfig | None,
        remote_name: str,
        arguments: Mapping[str, Any],
        decision: PolicyDecision,
        confirmed: bool | None,
        channel: str | None = None,
    ) -> None:
        self._write(
            {
                "event": "decision",
                **_server_fields(server, remote_name),
                "decision": decision.action,
                "rule": decision.rule,
                "risk": decision.risk,
                "confirmed": confirmed,
                **({"channel": channel} if channel else {}),
                "arguments": hash_arguments(arguments),
            }
        )

    def approval(self, entry: Mapping[str, Any], *, approved: bool, reason: str | None = None, channel: str | None = None) -> None:
        """How one question put to a person ended. Never its text: that quotes arguments."""

        self._write(
            {
                "event": "approval",
                "tool": entry.get("operation"),
                "fingerprint": entry.get("fingerprint"),
                "risk": entry.get("risk"),
                "scopes": list(entry.get("scopes") or []),
                "approved": approved,
                "ended": reason,
                "channel": channel,
            }
        )

    def call(
        self,
        *,
        server: ServerConfig | None,
        remote_name: str,
        outcome: str,
        duration_ms: float,
        error_type: str | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "event": "call",
            **_server_fields(server, remote_name),
            "outcome": outcome,
            "duration_ms": duration_ms,
        }
        if error_type is not None:
            record["error_type"] = error_type
        self._write(record)

    def _write(self, record: dict[str, Any]) -> None:
        line = json.dumps(
            {"time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **record},
            ensure_ascii=False,
        )
        with self._lock:
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError as error:
                if not self._warned:
                    self._warned = True
                    print(f"[odessy] cannot write the audit log {self.path}: {error}", file=sys.stderr)


def _server_fields(server: ServerConfig | None, remote_name: str) -> dict[str, Any]:
    if server is None:
        return {"server": "builtin", "tool": remote_name}
    return {
        "server": server.name,
        "trust": server.trust,
        "transport": server.transport,
        "tool": remote_name,
    }


_current: ContextVar[ExternalAuditLog | None] = ContextVar("external_audit", default=None)


@contextmanager
def audit_scope(log: ExternalAuditLog | None) -> Iterator[None]:
    """Record this run's calls in ``log`` (nothing outside the block)."""

    token = _current.set(log)
    try:
        yield
    finally:
        _current.reset(token)


def current_audit() -> ExternalAuditLog | None:
    return _current.get()
