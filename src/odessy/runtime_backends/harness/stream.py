"""Normalize the runtime's live frames into typed stream events (prompt 05).

Two channels reach an Odessy turn, and they must not be confused:

- **transient** — the bridges plugin forwards the runtime's own ``agent/assistant-stream``
  frames (``start`` / ``chunk`` / ``end``) over a loopback socket. They are process-local
  and may be dropped, reordered by retry, or arrive after the turn ended.
- **committed** — ACP ``session/update`` notifications and the ``session/prompt`` result.
  These are the authority: the answer Odessy shows once, stores, and speaks.

:class:`TurnStream` keeps them separate. Deltas never become the answer; the answer is
never assembled out of deltas. It also owns the bounds (one frame, one turn), the counters
that make loss visible, and the one terminal outcome per turn.

Nothing here logs or returns content in a diagnostic: text is only in :class:`Delta` and in
the settlement's ``text``, which the caller already holds as the turn's result.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

#: Largest single delta accepted from the bridge. A frame beyond it is malformed, not text.
MAX_DELTA_CHARS = 64 * 1024
#: Largest text retained for one turn — **across every attempt**. Beyond it further deltas
#: are dropped and the settlement says so; the committed answer is unaffected.
MAX_TURN_CHARS = 1 << 20
#: The only stop reason in which an attempt's text became the answer. Anything else
#: (``error``, ``cancelled``, a refusal) leaves that attempt partial, because its frames
#: describe text that was never committed.
COMPLETE_STOP_REASON = "end_turn"


@dataclass(frozen=True)
class Delta:
    """One visible piece of text, before the answer is committed."""

    seq: int
    text: str
    stream: str
    attempt: int
    turn: Any
    received_at: float

    def public(self) -> dict[str, Any]:
        """Return the safe public representation without private runtime state."""
        return {"stream": self.stream, "seq": self.seq, "text": self.text,
                "attempt": self.attempt, "turn": self.turn}


@dataclass(frozen=True)
class ToolProgress:
    """Tool activity reported by the runtime (name and state only, never arguments)."""

    seq: int
    name: str
    state: str
    call_id: str
    turn: Any = None

    def public(self) -> dict[str, Any]:
        """Return the safe public representation without private runtime state."""
        return {"seq": self.seq, "name": self.name, "state": self.state,
                "call_id": self.call_id, "turn": self.turn}


@dataclass(frozen=True)
class Attempt:
    """One provider attempt inside a turn; a retry leaves the earlier one partial."""

    number: int
    chars: int
    partial: bool

    def public(self) -> dict[str, Any]:
        """Return the safe public representation without private runtime state."""
        return {"number": self.number, "chars": self.chars, "partial": self.partial}


@dataclass(frozen=True)
class Settlement:
    """The one terminal record of a turn: what was committed, and what happened to it."""

    turn: Any
    text: str
    stop_reason: str
    attempts: tuple[Attempt, ...] = ()
    deltas: int = 0
    dropped: int = 0
    late: int = 0
    malformed: int = 0
    foreign: int = 0
    truncated: bool = False
    first_delta_ms: float | None = None
    settled_ms: float = 0.0

    @property
    def partial_attempts(self) -> int:
        """Count attempts which ended without a complete answer."""
        return sum(1 for attempt in self.attempts if attempt.partial)

    def public(self) -> dict[str, Any]:
        """Safe to log or expose: counts and timings, never the text."""
        return {"turn": self.turn, "stop_reason": self.stop_reason,
                "deltas": self.deltas, "dropped": self.dropped, "late": self.late,
                "malformed": self.malformed, "foreign": self.foreign,
                "truncated": self.truncated, "partial_attempts": self.partial_attempts,
                "first_delta_ms": self.first_delta_ms, "settled_ms": self.settled_ms,
                "text_chars": len(self.text)}


@dataclass
class _Collector:
    number: int = 0
    chars: int = 0
    closed: bool = False


def _turn_of(message: Mapping[str, Any]) -> int | None:
    """The bridge turn number a frame belongs to, when it names one.

    The plugin writes it on chunk frames directly and inside the lifecycle frame for
    ``start``/``end``. A frame that names none cannot be attributed, so it is treated as
    belonging to the turn that is running (the transport is ordered within a session).
    """
    def as_turn(value: Any) -> int | None:
        """Project a transient attempt into its bounded turn representation."""
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    turn = as_turn(message.get("turn"))
    if turn is None:
        frame = message.get("frame")
        if isinstance(frame, Mapping):
            turn = as_turn(frame.get("turn"))
    return turn


class TurnStream:
    """The translator for one turn. Feeds on bridge frames, then settles exactly once.

    Thread-safety: the bridge pump feeds from its reader thread while the turn thread
    waits for the prompt result and then settles, so every entry point takes the same
    short lock.
    """

    def __init__(self, session_id: str, *, stream_id: str = "",
                 previous_turn: Any = None,
                 max_delta_chars: int = MAX_DELTA_CHARS,
                 max_turn_chars: int = MAX_TURN_CHARS):
        """Initialize the owned state and synchronization used by this component."""
        self.session_id = session_id
        self.stream_id = stream_id or session_id
        #: Bridge turn numbers up to this one belong to a turn that has already settled.
        #: A frame carrying one of them is a straggler, not text for this turn.
        self._previous_turn = previous_turn if isinstance(previous_turn, int) else None
        self._max_delta = max_delta_chars
        self._max_turn = max_turn_chars
        self._lock = threading.Lock()
        self._seq = 0
        self._attempt = 0
        self._collector = _Collector()
        self._attempts: list[Attempt] = []
        self._completed = False
        self._settled: Settlement | None = None
        self._deltas = 0
        self._dropped = 0
        self._late = 0
        self._malformed = 0
        self._foreign = 0
        self._truncated = False
        #: Text accepted across *all* attempts of this turn: the 1 MiB bound is per turn,
        #: so a retry does not get a fresh allowance.
        self._turn_chars = 0
        #: Highest bridge turn number seen here; the next turn uses it as its floor.
        self._turn: int | None = None
        self._first_delta_at: float | None = None
        self._started_at = time.monotonic()

    # ------------------------------------------------------------------ properties

    @property
    def settled(self) -> bool:
        """Report whether this transient stream has a terminal settlement."""
        return self._settled is not None

    @property
    def attempts(self) -> tuple[Attempt, ...]:
        """Return the recorded attempt projections for this turn."""
        with self._lock:
            return tuple(self._attempts)

    @property
    def last_turn(self) -> int | None:
        """Highest bridge turn number this stream saw, for the next turn's floor."""
        with self._lock:
            return self._turn

    def snapshot(self) -> dict[str, Any]:
        """Return the application projection without creating a second runtime history."""
        with self._lock:
            return {"deltas": self._deltas, "dropped": self._dropped, "late": self._late,
                    "malformed": self._malformed, "foreign": self._foreign,
                    "truncated": self._truncated, "attempts": [a.public() for a in self._attempts],
                    "settled": self._settled is not None}

    # ---------------------------------------------------------------------- input

    def feed(self, message: Mapping[str, Any]) -> list[Delta | ToolProgress]:
        """Translate one bridge message. Malformed, foreign and late input is counted."""
        with self._lock:
            if not isinstance(message, Mapping):
                self._malformed += 1
                return []
            if message.get("type") == "hello":
                return []  # handshake, already handled by the client
            if message.get("type") == "dropped":
                # The producer's queue was full: frames were lost before they reached this
                # process. Counted as transport loss, not as unreadable input.
                lost = message.get("frames")
                self._dropped += int(lost) if isinstance(lost, int) and lost > 0 else 1
                self._truncated = True
                return []
            if message.get("type") != "frame":
                self._malformed += 1
                return []
            session_id = message.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                # A frame that does not say which session it belongs to cannot be
                # attributed, so it is malformed — not another session's frame.
                self._malformed += 1
                return []
            if session_id != self.session_id:
                # Another session's frames are not ours to render (interleaved sessions).
                self._foreign += 1
                return []
            frame_turn = _turn_of(message)
            if (frame_turn is not None and self._previous_turn is not None
                    and frame_turn <= self._previous_turn):
                # A frame from a turn that has already settled. Draining the queue at
                # start cannot catch one that arrives afterwards, so the turn number is
                # checked on every frame; anything else would render the previous turn's
                # text into this one.
                self._late += 1
                return []
            if frame_turn is not None:
                self._turn = frame_turn if self._turn is None else max(self._turn, frame_turn)
            if self._settled is not None:
                self._late += 1
                return []
            received = time.monotonic()
            lifecycle = message.get("frame")
            if isinstance(lifecycle, Mapping):
                return self._lifecycle(lifecycle, message.get("turn"))
            return self._chunk(message, received)

    def _lifecycle(self, frame: Mapping[str, Any], turn: Any) -> list[Delta | ToolProgress]:
        """Apply one ordered start/end frame to the active stream attempt."""
        kind = str(frame.get("type") or "")
        if kind == "start":
            if self._collector.number and not self._collector.closed:
                # A retry begins before the previous attempt ended: that attempt stays
                # visibly partial rather than being merged into the new one.
                self._close_attempt(partial=True)
            self._attempt += 1
            self._collector = _Collector(number=self._attempt)
            return []
        if kind == "end":
            # The runtime ends every stream, including one whose turn then fails, so this
            # says "no more text", not "this became the answer". ``settle`` demotes the
            # attempt to partial when the turn did not end normally.
            self._close_attempt(partial=False)
            return []
        self._malformed += 1
        return []

    def _chunk(self, message: Mapping[str, Any], received: float) -> list[Delta | ToolProgress]:
        """Apply a validated delta while enforcing text and identity bounds."""
        chunk = message.get("chunk")
        if not isinstance(chunk, Mapping):
            self._malformed += 1
            return []
        kind = str(chunk.get("type") or "")
        if kind == "reasoning-delta":
            # Reasoning is not part of the answer and is not shown as text. It is counted
            # so a consumer can see it happened, but no content is retained.
            if not self._collector.number:
                self._attempt = max(self._attempt, 1)
                self._collector = _Collector(number=self._attempt)
            return []
        if kind != "text-delta":
            self._malformed += 1
            return []
        text = chunk.get("text")
        if not isinstance(text, str):
            self._malformed += 1
            return []
        if len(text) > self._max_delta:
            self._malformed += 1
            self._truncated = True
            return []
        if self._turn_chars + len(text) > self._max_turn:
            # Per *turn*, not per attempt: a retry does not reset the allowance, or a reply
            # that keeps failing over would keep thousands of attempts' worth of text.
            self._dropped += 1
            self._truncated = True
            return []
        if not self._collector.number:
            # A chunk before any ``start`` still belongs to the first attempt.
            self._attempt = max(self._attempt, 1)
            self._collector = _Collector(number=self._attempt)
        self._seq += 1
        self._deltas += 1
        self._collector.chars += len(text)
        self._turn_chars += len(text)
        if self._first_delta_at is None:
            self._first_delta_at = received
        return [Delta(seq=self._seq, text=text, stream=self.stream_id,
                      attempt=self._collector.number, turn=message.get("turn"),
                      received_at=received)]

    def feed_notification(self, notification: Mapping[str, Any]) -> list[ToolProgress]:
        """Tool activity from the ACP channel (name and state only)."""
        with self._lock:
            params = notification.get("params") or {}
            if notification.get("method") != "session/update":
                return []
            if self._settled is not None:
                self._late += 1
                return []
            update = params.get("update") or {}
            kind = str(update.get("sessionUpdate") or "")
            if kind not in {"tool_call", "tool_call_update"}:
                return []
            call = update.get("toolCall") or {}
            name = str(update.get("title") or call.get("title") or call.get("name") or "")
            state = str(update.get("status") or call.get("status") or kind)
            call_id = str(update.get("toolCallId") or call.get("toolCallId") or "")
            self._seq += 1
            return [ToolProgress(seq=self._seq, name=name, state=state, call_id=call_id,
                                 turn=params.get("turn"))]

    def note_dropped(self, count: int = 1) -> None:
        """The transport dropped frames (bounded queue); the loss is reported, not hidden."""
        lost = max(0, int(count))
        if not lost:
            # Nothing was lost: a turn that lost nothing is not a truncated turn.
            return
        with self._lock:
            self._dropped += lost
            self._truncated = True

    def note_late(self, count: int = 1) -> None:
        """Frames that belong to an earlier turn were discarded before this one began."""
        with self._lock:
            self._late += max(0, int(count))

    def _close_attempt(self, *, partial: bool) -> None:
        """Finish the current attempt without reclassifying partial text as committed."""
        if not self._collector.number or self._collector.closed:
            return
        self._collector.closed = True
        self._attempts.append(Attempt(number=self._collector.number,
                                      chars=self._collector.chars, partial=partial))
        self._completed = self._completed or not partial

    # ---------------------------------------------------------------------- output

    def settle(self, *, text: str, stop_reason: str, turn: Any = None) -> Settlement:
        """Close the turn once. A second call returns the first settlement unchanged."""
        with self._lock:
            if self._settled is not None:
                return self._settled
            complete = stop_reason == COMPLETE_STOP_REASON
            if self._collector.number and not self._collector.closed:
                # Whatever was still open when the turn ended: only a normal end makes it
                # a finished attempt. A cancelled or failed turn leaves it partial.
                self._close_attempt(partial=not complete)
            elif not complete and self._attempts and not self._attempts[-1].partial:
                # An ``end`` frame closed the last attempt, then the turn failed or was
                # cancelled. Text from a turn that did not end normally never became the
                # answer, so the attempt is not complete.
                last = self._attempts[-1]
                self._attempts[-1] = Attempt(number=last.number, chars=last.chars,
                                             partial=True)
            now = time.monotonic()
            settlement = Settlement(
                turn=turn,
                text=text,
                stop_reason=stop_reason,
                attempts=tuple(self._attempts),
                deltas=self._deltas,
                dropped=self._dropped,
                late=self._late,
                malformed=self._malformed,
                foreign=self._foreign,
                truncated=self._truncated,
                first_delta_ms=(None if self._first_delta_at is None
                                else round((self._first_delta_at - self._started_at) * 1000, 3)),
                settled_ms=round((now - self._started_at) * 1000, 3),
            )
            self._settled = settlement
            return settlement
