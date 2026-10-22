"""What left this machine: one record per request Odessy sent to a cloud provider.

One JSON object per line, appended to ``~/.local/state/odessy/egress.jsonl``
(``ODESSY_EGRESS_FILE`` sets another path; an empty value turns the log off). The log
answers "what of mine was sent, to whom" without becoming a second copy of it, so it
holds **no content**: no request text, no tool output, no answer. Two kinds of record:

* ``model_request``: one request to a cloud model. The provider, host, and model; what
  kinds of data the request carried (the number of messages, earlier spoken turns, and
  tool results, and the characters sent); tokens; outcome; duration; whether the reply
  was streamed.
* ``failover``: a turn moved from one backend to another because the first was
  unavailable, and whether the new one is local.

``python -m odessy egress`` prints the recent records. The third-party MCP audit log
(``audit.py``) is separate: it records tool calls, this records model traffic.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EGRESS_FILE_ENV = "ODESSY_EGRESS_FILE"


def default_egress_file() -> Path:
    return Path.home() / ".local" / "state" / "odessy" / "egress.jsonl"


def egress_file(environ: Mapping[str, str] | None = None) -> Path | None:
    """The configured egress file, or None when the log is turned off."""

    source = os.environ if environ is None else environ
    if EGRESS_FILE_ENV in source:
        explicit = source[EGRESS_FILE_ENV].strip()
        return Path(explicit).expanduser() if explicit else None
    return default_egress_file()


class EgressLog:
    """Appends egress records. A failed write is reported once and does not stop the run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._warned = False

    def model_request(
        self,
        *,
        provider: str,
        model: str,
        host: str,
        sent: Mapping[str, int],
        outcome: str,
        duration_ms: float,
        streamed: bool,
        tokens: Mapping[str, int | None],
        error_type: str | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "event": "model_request",
            "provider": provider,
            "model": model,
            "host": host,
            "sent": dict(sent),
            "tokens": {name: value for name, value in tokens.items() if value is not None},
            "streamed": streamed,
            "outcome": outcome,
            "duration_ms": round(duration_ms, 1),
        }
        if error_type is not None:
            record["error_type"] = error_type
        self._write(record)

    def failover(self, *, from_backend: str, to_backend: str, to_local: bool, error_type: str) -> None:
        self._write(
            {
                "event": "failover",
                "from": from_backend,
                "to": to_backend,
                "to_local": to_local,
                "error_type": error_type,
            }
        )

    def _write(self, record: dict[str, Any]) -> None:
        line = json.dumps(
            {"time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **record},
            ensure_ascii=False,
        )
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError as error:
                if not self._warned:
                    self._warned = True
                    print(
                        f"[odessy] cannot write the egress log {self.path}: {error}. "
                        f"Set {EGRESS_FILE_ENV} to another path, or to an empty value to turn it off.",
                        file=sys.stderr,
                    )


_lock = threading.Lock()
_cached: tuple[Path | None, EgressLog | None] | None = None


def egress_log() -> EgressLog | None:
    """The process's egress log for the current ``ODESSY_EGRESS_FILE``, or None when off."""

    global _cached
    path = egress_file()
    with _lock:
        if _cached is None or _cached[0] != path:
            _cached = (path, EgressLog(path) if path is not None else None)
        return _cached[1]


def read_records(path: Path, limit: int) -> list[dict[str, Any]]:
    """The last ``limit`` records in ``path``; lines that are not JSON objects are skipped."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records[-limit:] if limit > 0 else records


def format_record(record: Mapping[str, Any]) -> str:
    """One line for ``odessy egress``."""

    time = str(record.get("time", ""))[:19].replace("T", " ")
    if record.get("event") == "failover":
        where = "local" if record.get("to_local") else "cloud"
        return f"{time}  failover {record.get('from')} -> {record.get('to')} ({where}; {record.get('error_type')})"
    sent = record.get("sent") or {}
    tokens = record.get("tokens") or {}
    parts = [
        f"{time}  {record.get('provider')}/{record.get('model')} @ {record.get('host')}",
        f"{record.get('outcome')}",
        f"{sent.get('chars', 0)} chars",
        f"{sent.get('history_turns', 0)} earlier turns",
        f"{sent.get('tool_results', 0)} tool results",
    ]
    if tokens:
        parts.append(f"{tokens.get('prompt', 0)} in / {tokens.get('completion', 0)} out tokens")
    if record.get("streamed"):
        parts.append("streamed")
    return "  ".join(parts[:1]) + "  " + ", ".join(parts[1:])
