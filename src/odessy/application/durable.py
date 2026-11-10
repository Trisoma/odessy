"""Durable *application* records for a session — never a second transcript.

The Harness runtime owns durable model history. It keeps it in a versioned, compressed log of
its own and restores it through ``session/resume``; the probe evidence
(``experiments/restructure/harness_compatibility/probe_sessions.py``) shows a resumed turn is
answered with the earlier conversation in context, and that the client is told **nothing** about
that history. So there are exactly two things Odessy has to keep:

* **Identity**, so a reconnecting application can find the runtime session it belongs to and
  refuse to talk to a different one: the Odessy session id, the engine and its pinned identity,
  the runtime session id, and the workspace the runtime bound it to.
* **A bounded projection** of what was *settled* for the client — the transcript turns it was
  shown and their event cursors — so a reconnection can redisplay settled content and continue
  the cursor sequence. Transient deltas are not persisted: they are not settlement.

What this module deliberately does **not** do:

* It does not store the model's conversation, and nothing here is ever sent to a model. The
  runtime's log is the only model transcript, and it is never parsed or rewritten here — the file
  is a versioned, compressed format owned by the runtime.
* It does not compact, summarise or migrate runtime history. An unknown or older record schema is
  reported, not converted.

Writes are atomic (temporary file then replace) and the previous version is kept as ``.bak``, so a
crash mid-write cannot leave a half-record that a later restore would trust.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from odessy.authority.redaction import looks_like_secret_name, redact_text

#: Record schema this build writes and the only one it reads. A record written by a newer build
#: is refused rather than guessed at; a record from an older schema is refused too, because this
#: build implements no migration and inventing one would be a silent reinterpretation of history.
SCHEMA = 1

#: Session ids are opaque hex from ``uuid4().hex``. Validating them keeps a caller-supplied id
#: from addressing a path outside the store.
_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")

#: Turn states, in the order a turn moves through them.
#: ``queued``   accepted by the application, not yet handed to the engine.
#: ``admitted`` handed to the engine, no settlement recorded: an interrupted turn after a crash.
#: ``settled``  the engine produced an answer that was shown to the client.
#: ``stopped``  a cancellation was confirmed (or reported as unconfirmed) by the run.
#: ``failed``   the engine raised; whatever it produced is not an answer.
QUEUED, ADMITTED, SETTLED, STOPPED, FAILED = (
    "queued", "admitted", "settled", "stopped", "failed")

#: What a restored session reports for a turn it found unfinished.
NOT_SENT, INTERRUPTED = "not_sent", "interrupted"

#: Event types worth keeping: settlement and the decisions around it. Deltas, progress and
#: routing notes are transient by contract and are not stored.
#: ``result`` is left out on purpose: its text is the transcript entry's text, and storing both
#: would be a second copy of the answer.
DURABLE_EVENTS = frozenset({
    "transcript", "selection", "stopped", "error", "durability", "cursor_gap",
})


def default_state_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Where Odessy keeps its own session records, mirroring the Harness home's rule."""
    environ = os.environ if environ is None else environ
    override = environ.get("ODESSY_SESSION_STATE_DIR")
    if override:
        return Path(override)
    from odessy.runtime_backends.harness.config import default_home
    return default_home(environ).parent.parent / "sessions"


def default_store(**kwargs: Any) -> "SessionStore":
    """The store a coordinator uses unless one is injected (tests, alternate profiles)."""
    return SessionStore(default_state_dir(), **kwargs)


def _secret_values(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Credential values this process knows, longest first, for a last-pass scrub.

    The controller already replaces the values of the profiles it knows about. This is the
    store's own guard, so a caller that forgets cannot write a key to disk: the record is the
    one artefact that outlives the process.
    """
    environ = os.environ if environ is None else environ
    values = {value for name, value in environ.items()
              if value and len(value) >= 12 and looks_like_secret_name(name)}
    return tuple(sorted(values, key=len, reverse=True))


def _sanitise(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Redact again on the way to disk; the caller may not have passed through ``public``."""
    if isinstance(value, str):
        value = redact_text(value)
        for secret in secrets:
            value = value.replace(secret, "<redacted>")
        return value
    if isinstance(value, Mapping):
        return {str(key): _sanitise(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitise(item, secrets) for item in value]
    return value


class RuntimeSessionUnavailable(RuntimeError):
    """A durable session exists under this identity but this build cannot continue it.

    Raised instead of starting a fresh session: the caller asked to continue an identity, and
    answering that by silently beginning a new conversation is the failure mode this whole
    module exists to prevent.
    """

    def __init__(self, verdict: str, reason: str):
        self.verdict = verdict
        self.reason = reason
        super().__init__(f"cannot continue this session: {reason}")

    def public(self) -> dict[str, str]:
        return {"status": "unavailable", "verdict": self.verdict, "reason": self.reason}


@dataclass
class Loaded:
    """The outcome of reading a record. ``verdict`` is the restore decision, not an exception."""

    session: str
    verdict: str
    record: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    backup: Path | None = None

    @property
    def usable(self) -> bool:
        return self.verdict == "ok"

    def public(self) -> dict[str, Any]:
        return {"session": self.session, "verdict": self.verdict, "reason": self.reason}


class SessionStore:
    """One directory of session records, one JSON file per Odessy session."""

    def __init__(self, root: str | os.PathLike[str] | None = None, *,
                 projection_limit: int = 200, projection_bytes: int = 262_144,
                 turn_limit: int = 64):
        if min(projection_limit, projection_bytes, turn_limit) < 1:
            raise ValueError("Projection bounds must be positive")
        self.root = Path(root) if root is not None else default_state_dir()
        self.projection_limit = projection_limit
        self.projection_bytes = projection_bytes
        self.turn_limit = turn_limit

    # ------------------------------------------------------------------ paths

    def path(self, session: str) -> Path:
        if not isinstance(session, str) or not _SESSION_ID.match(session):
            raise ValueError("A session id must be 32 lowercase hex characters")
        return self.root / f"{session}.json"

    def _backup_path(self, session: str) -> Path:
        return self.root / f"{session}.json.bak"

    # ------------------------------------------------------------------ reading

    def load(self, session: str) -> Loaded:
        """Read one record, classifying what was found instead of raising."""
        path = self.path(session)
        if not path.exists():
            return Loaded(session, "missing", reason="no record for this session on this machine")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            return Loaded(session, "unreadable", reason=f"the record could not be read: {exc}")
        if not isinstance(raw, dict):
            return Loaded(session, "unreadable", reason="the record is not an object")
        schema = raw.get("schema")
        if not isinstance(schema, int) or isinstance(schema, bool):
            return Loaded(session, "unreadable", reason="the record declares no schema version")
        if schema != SCHEMA:
            # Not migrated, not reinterpreted: a newer build's record may mean something
            # different, and an older one may be missing fields this build relies on.
            newer = schema > SCHEMA
            return Loaded(session, "incompatible",
                          reason=f"record schema {schema} is {'newer' if newer else 'older'} "
                                 f"than this build's {SCHEMA}; it is not converted")
        if raw.get("session") != session:
            return Loaded(session, "unreadable", reason="the record names a different session")
        if not _shape_ok(raw):
            return Loaded(session, "unreadable", reason="the record is missing required fields")
        return Loaded(session, "ok", record=raw, backup=self._backup_path(session))

    def list_records(self) -> list[dict[str, Any]]:
        """Summaries of the records a caller could restore, newest first.

        Only metadata and cursors: a listing must never leak stored text, so the projection is
        counted, not returned.
        """
        if not self.root.is_dir():
            return []
        summaries: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            session = path.stem
            if not _SESSION_ID.match(session):
                continue
            loaded = self.load(session)
            if not loaded.usable:
                summaries.append({"session": session, "verdict": loaded.verdict,
                                  "reason": loaded.reason})
                continue
            record = loaded.record
            summaries.append({
                "session": session,
                "verdict": "ok",
                "engine": (record.get("engine") or {}).get("name", ""),
                "runtime_session": (record.get("runtime") or {}).get("session_id", ""),
                "workspace": (record.get("runtime") or {}).get("workspace", ""),
                "created": record.get("created"),
                "updated": record.get("updated"),
                "closed": bool(record.get("closed")),
                "turns": len(record.get("turns") or []),
                "cursor": dict(record.get("cursor") or {}),
                "incomplete": [turn.get("id") for turn in restore_state(record)["incomplete"]],
            })
        summaries.sort(key=lambda item: item.get("updated") or 0, reverse=True)
        return summaries

    # ------------------------------------------------------------------ writing

    def new_record(self, session: str, *, engine: str, workspace: str) -> dict[str, Any]:
        now = time.time()
        return {
            "schema": SCHEMA,
            "session": session,
            "engine": {"name": engine, "identity": {}},
            "runtime": {"session_id": "", "workspace": workspace, "protocol": None},
            "profile": "",
            "created": now,
            "updated": now,
            "closed": False,
            "cursor": {"first": 0, "last": 0, "dropped": 0},
            "events": [],
            "turns": [],
        }

    def save(self, record: dict[str, Any]) -> None:
        """Write atomically, keeping the previous version beside it."""
        session = record.get("session")
        path = self.path(session)  # validates the id
        self.root.mkdir(parents=True, exist_ok=True)
        record = dict(record)
        record["schema"] = SCHEMA
        record["updated"] = time.time()
        payload = json.dumps(_sanitise(record, _secret_values()), ensure_ascii=False,
                             sort_keys=True)
        temporary = path.with_suffix(".json.tmp")
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            # The previous record stays readable if the new one turns out to be wrong, and a
            # migration (none is implemented here) would have something to fall back to.
            try:
                os.replace(path, self._backup_path(session))
            except OSError:
                pass
        os.replace(temporary, path)

    def forget(self, session: str) -> bool:
        """Retire a record without destroying it, so a stale id cannot be restored by accident."""
        path = self.path(session)
        if not path.exists():
            return False
        os.replace(path, self.root / f"{session}.closed.json")
        return True

    # ------------------------------------------------------------------ projection

    def append_event(self, record: dict[str, Any], event: Mapping[str, Any]) -> bool:
        """Add one settled event to the bounded projection. Returns whether it was kept."""
        events = record.setdefault("events", [])
        events.append({"id": int(event["id"]), "type": str(event["type"]),
                       "time": event.get("time"), "data": dict(event.get("data") or {})})
        return self._trim(record)

    def _trim(self, record: dict[str, Any]) -> bool:
        events = record.setdefault("events", [])
        cursor = record.setdefault("cursor", {"first": 0, "last": 0, "dropped": 0})
        dropped = 0
        while events and (len(events) > self.projection_limit
                          or _size(events) > self.projection_bytes):
            events.pop(0)
            dropped += 1
        if events:
            cursor["first"] = int(events[0]["id"])
            cursor["last"] = int(events[-1]["id"])
        if dropped:
            cursor["dropped"] = int(cursor.get("dropped") or 0) + dropped
        return bool(dropped)

    def add_turn(self, record: dict[str, Any], turn_id: str, status: str, *,
                 text: str = "") -> dict[str, Any] | None:
        """Record a turn's transition. Idempotent per ``turn_id``; never regresses a status."""
        turns = record.setdefault("turns", [])
        now = time.time()
        found = next((turn for turn in turns if turn.get("id") == turn_id), None)
        if found is None:
            # Created in the state it is being recorded in, with the timestamps that state
            # implies: a turn born ``admitted`` never claimed to have been queued.
            found = {"id": turn_id, "status": QUEUED, "queued": None, "admitted": None,
                     "settled": None, "cursor": None, "text": text[:200]}
            turns.append(found)
            if len(turns) > self.turn_limit:
                del turns[:len(turns) - self.turn_limit]
        order = {QUEUED: 0, ADMITTED: 1, SETTLED: 2, STOPPED: 2, FAILED: 2}
        if order.get(status, 0) < order.get(str(found.get("status")), 0):
            return found
        found["status"] = status
        if status == QUEUED and not found.get("queued"):
            found["queued"] = now
        if status == ADMITTED and not found.get("admitted"):
            found["admitted"] = now
        if status in (SETTLED, STOPPED, FAILED):
            found["settled"] = now
            found["cursor"] = int((record.get("cursor") or {}).get("last") or 0)
        return found


def _size(events: Iterable[Mapping[str, Any]]) -> int:
    return len(json.dumps(list(events), ensure_ascii=False))


def _shape_ok(record: Mapping[str, Any]) -> bool:
    cursor = record.get("cursor")
    return (isinstance(record.get("events"), list)
            and isinstance(record.get("turns"), list)
            and isinstance(record.get("engine"), dict)
            and isinstance(record.get("runtime"), dict)
            and isinstance(cursor, dict)
            and isinstance(cursor.get("first"), int)
            and isinstance(cursor.get("last"), int))


def restore_state(record: Mapping[str, Any]) -> dict[str, Any]:
    """What a restored session reports about the turns it found unfinished.

    Three outcomes are kept apart, because they mean different things to a person:

    * ``queued`` never reached the engine, so nothing ran and nothing is replayed.
    * ``admitted`` reached the engine without a settlement: work may or may not have happened,
      and the honest report is *interrupted*, not "failed" and certainly not "retry".
    * ``settled``/``stopped``/``failed`` are complete records and need no report.
    """
    incomplete = []
    for turn in record.get("turns") or []:
        status = str(turn.get("status") or "")
        if status == QUEUED:
            incomplete.append({"id": turn.get("id"), "state": NOT_SENT,
                               "text": turn.get("text", ""), "status": status})
        elif status == ADMITTED:
            incomplete.append({"id": turn.get("id"), "state": INTERRUPTED,
                               "text": turn.get("text", ""), "status": status})
    cursor = dict(record.get("cursor") or {})
    return {"incomplete": incomplete,
            "first_cursor": int(cursor.get("first") or 0),
            "last_cursor": int(cursor.get("last") or 0),
            "dropped": int(cursor.get("dropped") or 0)}


__all__ = ["SCHEMA", "SessionStore", "Loaded", "RuntimeSessionUnavailable", "DURABLE_EVENTS",
           "default_state_dir", "default_store", "restore_state", "QUEUED", "ADMITTED",
           "SETTLED", "STOPPED", "FAILED", "NOT_SENT", "INTERRUPTED"]
