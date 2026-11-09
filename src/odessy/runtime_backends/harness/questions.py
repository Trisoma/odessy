"""Answer the runtime's clarification questions through Odessy's existing broker.

The pinned runtime already owns the model-facing side: its own ``@deepseek-ai/dsh-tool-ask-user``
registers ``ask_user_question`` and calls the documented ``ctx.userQuestions.ask(...)`` seam.
The packaging bundle mounts that tool and registers one answerer; this module is the Python
half of that answerer's transport.

What this module owns:

* reading one ``{"type":"question", ...}`` line from the session's bridge socket;
* validating it against the runtime session, the open turn and a unique request id, with
  bounded question count, text, options and pending requests;
* asking the human through :class:`odessy.interaction.broker.InteractionBroker` — the same
  broker and the same ``POST /v1/sessions/{sid}/questions/{id}`` route the desktop already
  uses — one question at a time;
* returning the authenticated answer to the exact waiting tool invocation, so the runtime
  resumes the *same* turn;
* settling a waiter as an error when the answer is not a real human answer: cancellation,
  Stop, turn end, timeout, bridge loss or session close. An answer is never fabricated.

It deliberately does not reason about the model's prose, run an event loop of its own, or
grant any authority. A clarification answer is information; only the approval bridge can
authorize an operation.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Callable

#: Mirrors the limit the packaged plugin enforces, so both ends reject the same requests.
MAX_QUESTIONS = 8
MAX_QUESTION_TEXT = 8192
MAX_OPTIONS = 32
MAX_OPTION_LABEL = 1024
MAX_PENDING = 4
MAX_REQUEST_ID = 128
#: A backstop around the broker's own bounded wait; the broker normally settles first.
DEFAULT_WAIT_SECONDS = 300.0


class QuestionFrameError(ValueError):
    """A question frame cannot be bound to one exact runtime turn."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Question:
    """One question projected into the broker's existing schema."""

    id: str
    text: str
    choices: tuple[str, ...]
    header: str | None = None
    detail: str | None = None


@dataclass
class PendingQuestion:
    request_id: str
    turn: int
    session_id: str
    questions: tuple[Question, ...]
    stop_epoch: int
    state: str = "pending"
    task: asyncio.Task[Any] | None = None


@dataclass
class QuestionStats:
    """Counters that never contain question text or answers."""

    accepted: int = 0
    answered: int = 0
    cancelled: int = 0
    rejected: int = 0
    stale: int = 0
    timed_out: int = 0
    duplicate: int = 0

    def public(self) -> dict[str, int]:
        return dict(self.__dict__)


class _NoAnswer(Exception):
    """The broker did not produce a human answer (cancelled, expired, busy, closed)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:limit]


def parse_request(message: Any) -> tuple[Question, ...]:
    """Validate one question frame's payload, or raise :class:`QuestionFrameError`."""
    if not isinstance(message, Mapping):
        raise QuestionFrameError("the question frame was not an object")
    raw = message.get("questions")
    if not isinstance(raw, list) or not raw:
        raise QuestionFrameError("the question frame carried no questions")
    if len(raw) > MAX_QUESTIONS:
        raise QuestionFrameError(f"at most {MAX_QUESTIONS} questions are accepted")
    questions: list[Question] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise QuestionFrameError("a question was not an object")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 128:
            raise QuestionFrameError("each question needs a non-empty id of at most 128 characters")
        if identifier in seen:
            raise QuestionFrameError("question ids must be unique within one request")
        seen.add(identifier)
        text = item.get("question")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_QUESTION_TEXT:
            raise QuestionFrameError(
                f"a question must be 1..{MAX_QUESTION_TEXT} characters")
        if item.get("multiSelect") is True:
            # The broker collects one answer; pretending to collect several would silently
            # drop part of the question.
            raise QuestionFrameError("multi-select questions are not supported by this surface")
        choices: list[str] = []
        options = item.get("labels", item.get("options"))
        if options is not None:
            if not isinstance(options, list) or not options or len(options) > MAX_OPTIONS:
                raise QuestionFrameError(f"options must be 1..{MAX_OPTIONS} entries")
            for option in options:
                label = option.get("label") if isinstance(option, Mapping) else option
                if not isinstance(label, str) or not label.strip() or len(label) > MAX_OPTION_LABEL:
                    raise QuestionFrameError(
                        f"an option label must be 1..{MAX_OPTION_LABEL} characters")
                if label in choices:
                    raise QuestionFrameError("option labels must be distinct")
                choices.append(label)
        questions.append(Question(
            id=identifier, text=text, choices=tuple(choices),
            header=_text(item.get("header"), 80),
            detail=_text(item.get("detail"), MAX_QUESTION_TEXT),
        ))
    return tuple(questions)


class HarnessQuestionBridge:
    """One session's clarification channel: frame validation, binding, and the human wait.

    ``asker`` is :meth:`InteractionBroker.ask` (or a test double) with the broker's own
    signature ``(question, choices, allow_free_text) -> awaitable[dict]``.
    """

    def __init__(self, *, send: Callable[[Mapping[str, Any]], bool],
                 asker: Callable[[str, list[str] | None, bool], Any],
                 loop: Callable[[], asyncio.AbstractEventLoop | None],
                 epoch: Callable[[], int] | None = None,
                 is_closed: Callable[[], bool] | None = None,
                 wait_seconds: float = DEFAULT_WAIT_SECONDS):
        self._send = send
        self._asker = asker
        self._loop = loop
        self._epoch = epoch or (lambda: 0)
        self._is_closed = is_closed or (lambda: False)
        self._wait = wait_seconds
        self._lock = threading.RLock()
        self._turn: int | None = None
        self._session_id = ""
        self._floor = 0
        self._pending: dict[str, PendingQuestion] = {}
        self._decided: set[str] = set()
        self._closed = False
        self.stats = QuestionStats()

    # ------------------------------------------------------------------ turn scope

    def begin_turn(self, turn: int, *, session_id: str, turn_floor: int = 0) -> None:
        with self._lock:
            self._turn = int(turn)
            self._session_id = str(session_id or "")
            self._floor = int(turn_floor or 0)
            self._decided = set()

    def end_turn(self) -> None:
        """No new question can bind to a turn that ended; pending ones are cancelled."""
        self.cancel_all("turn ended")

    def close(self) -> None:
        self._closed = True
        self.cancel_all("session closed")

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"request_id": item.request_id, "turn": item.turn,
                     "questions": len(item.questions)}
                    for item in self._pending.values()]

    # -------------------------------------------------------------------- frames

    def observe(self, message: Mapping[str, Any]) -> None:
        """Pump thread: validate one frame and schedule the human wait on the session loop."""
        try:
            self._observe(message)
        except Exception:  # noqa: BLE001 - the pump thread must never die on an observer
            pass

    def _observe(self, message: Mapping[str, Any]) -> None:
        if not isinstance(message, Mapping) or message.get("type") != "question":
            return
        try:
            record = self._validate(message)
        except QuestionFrameError as exc:
            self._refuse(message.get("requestId"), exc.reason)
            return
        loop = self._loop()
        if loop is None or loop.is_closed() or self._closed:
            self._refuse(record.request_id, "the session cannot ask a human")
            return
        try:
            loop.call_soon_threadsafe(self._start, record)
        except RuntimeError:
            self._refuse(record.request_id, "the application loop is closed")

    def _validate(self, message: Mapping[str, Any]) -> PendingQuestion:
        with self._lock:
            if self._turn is None:
                raise QuestionFrameError("no turn is running")
            session_id = message.get("sessionId")
            if not isinstance(session_id, str) or session_id != self._session_id:
                # Another connection's session is another owner; no ancestry lookup.
                raise QuestionFrameError("the question names another session")
            request_id = message.get("requestId")
            if not isinstance(request_id, str) or not request_id or len(request_id) > MAX_REQUEST_ID:
                raise QuestionFrameError("the question carried no usable request id")
            turn = message.get("turn")
            if not isinstance(turn, int) or isinstance(turn, bool):
                raise QuestionFrameError("the question carried no runtime turn")
            if turn <= self._floor:
                raise QuestionFrameError("the question belongs to a finished turn")
            if request_id in self._pending or request_id in self._decided:
                self.stats.duplicate += 1
                raise QuestionFrameError("this question was already asked")
            if len(self._pending) >= MAX_PENDING:
                raise QuestionFrameError("too many questions are already waiting")
            questions = parse_request(message)
            record = PendingQuestion(
                request_id=request_id, turn=turn, session_id=session_id,
                questions=questions, stop_epoch=int(self._epoch() or 0))
            self._pending[request_id] = record
            self.stats.accepted += 1
            return record

    def _start(self, record: PendingQuestion) -> None:
        """Loop thread: own the wait for the human as one cancellable task."""
        with self._lock:
            live = self._pending.get(record.request_id)
            if live is None:
                return
        task = asyncio.create_task(self._ask(record))
        with self._lock:
            if self._pending.get(record.request_id) is record:
                record.state = "waiting"
                record.task = task

    async def _ask(self, record: PendingQuestion) -> None:
        answers: list[dict[str, Any]] = []
        try:
            for question in record.questions:
                result = await asyncio.wait_for(self._asker(question), self._wait)
                if not isinstance(result, Mapping) or result.get("status") != "ok":
                    reason = "cancelled"
                    if isinstance(result, Mapping) and isinstance(result.get("reason"), str):
                        reason = result["reason"]
                    raise _NoAnswer(reason)
                answer = result.get("answer")
                if not isinstance(answer, str) or not answer.strip():
                    raise _NoAnswer("the answer was empty")
                answers.append(self._map_answer(question, answer))
        except asyncio.TimeoutError:
            self._finish_cancel(record, "timeout", "timed_out")
            return
        except _NoAnswer as exc:
            self._finish_cancel(record, "cancelled" if exc.reason != "expired" else "timeout",
                                "cancelled")
            return
        except asyncio.CancelledError:
            self._finish_cancel(record, "cancelled", "cancelled")
            raise
        except Exception:  # noqa: BLE001 - a failed surface is not an answer
            self._finish_cancel(record, "failed", "rejected")
            return
        if not self._still_current(record):
            # The turn ended, a Stop landed, or the operation changed while the person was
            # answering: the answer is information for a turn that no longer exists.
            self._finish_cancel(record, "stale", "stale")
            return
        self._finish(record, "answered")
        sent = self._send({
            "type": "answer",
            "sessionId": record.session_id,
            "turn": record.turn,
            "requestId": record.request_id,
            "answers": answers,
        })
        if sent:
            with self._lock:
                self.stats.answered += 1
        else:
            with self._lock:
                self.stats.rejected += 1

    @staticmethod
    def _map_answer(question: Question, answer: str) -> dict[str, Any]:
        """A choice stays a choice; anything else is the human's own text."""
        if question.choices and answer in question.choices:
            return {"id": question.id, "selected": [answer]}
        return {"id": question.id, "selected": [], "custom": answer}

    def _still_current(self, record: PendingQuestion) -> bool:
        if self._closed or self._is_closed():
            return False
        if int(self._epoch() or 0) != record.stop_epoch:
            return False
        with self._lock:
            return self._pending.get(record.request_id) is record

    def _finish(self, record: PendingQuestion, state: str) -> None:
        with self._lock:
            live = self._pending.pop(record.request_id, None)
            if live is not None:
                record.state = state
                self._decided.add(record.request_id)

    def _finish_cancel(self, record: PendingQuestion, reason: str, counter: str) -> None:
        if self._finish_if_pending(record):
            self._send({"type": "cancel", "requestId": record.request_id, "reason": reason})
            with self._lock:
                setattr(self.stats, counter, getattr(self.stats, counter) + 1)

    def _finish_if_pending(self, record: PendingQuestion) -> bool:
        with self._lock:
            live = self._pending.pop(record.request_id, None)
            if live is None:
                return False
            record.state = "settled"
            self._decided.add(record.request_id)
            return True

    def _refuse(self, request_id: Any, reason: str) -> None:
        """A frame we cannot own is withdrawn, never answered."""
        with self._lock:
            if isinstance(request_id, str) and request_id:
                self._decided.add(request_id)
            self.stats.rejected += 1
        if isinstance(request_id, str) and request_id:
            self._send({"type": "cancel", "requestId": request_id, "reason": "rejected"})
        _ = reason  # the reason is for diagnostics only; it is not sent to the model

    def cancel_all(self, reason: str) -> None:
        """Withdraw every pending question (Stop, turn end, close)."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        with self._lock:
            records = list(self._pending.values())
        for record in records:
            if self._finish_if_pending(record):
                self._send({"type": "cancel", "requestId": record.request_id,
                            "reason": "stopped" if reason in ("stopped", "turn ended") else reason})
                # On the loop, stop the wait now; from a worker the broker's own cancel
                # releases it and the next turn starts clean.
                if record.task is not None and running is not None \
                        and record.task.get_loop() is running:
                    record.task.cancel()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "turn": self._turn,
                "pending": [item.request_id for item in self._pending.values()],
                "stats": self.stats.public(),
            }
