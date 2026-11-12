"""Model text spoken while the turn is still running.

A voice session sets a ``SpeechStream`` for its turn; a cloud adapter that streams its
reply feeds text into it as it arrives, and the stream hands each complete sentence to
the session's speaker. The first sentence is heard while the rest is still being
generated, instead of after the whole answer.

Speech is output only. Nothing here authorizes anything: a tool call in the same reply
still goes through validation, policy, and confirmation in the runtime. Text that comes
before a tool call (for example "Let me check the disk") is spoken; once a tool call
starts in a reply, the rest of that reply is not.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

#: Characters that end a sentence wherever they appear.
_HARD_ENDS = frozenset("。！？!?；;\n")
#: Where a long sentence without an ending may be split: a comma, else a space.
_COMMAS = "，,、"
DEFAULT_MAX_SENTENCE_CHARS = 160


class SpeechStream:
    """Splits streamed model text into sentences and emits each one once.

    ``emit`` is called from the model's worker thread, so it must be thread-safe (the
    voice session schedules onto its event loop). ``begin`` and ``end`` bracket one
    model reply; ``last_text`` is the whole text of the last reply that had no tool
    call, which the session compares with the turn's answer to avoid saying it twice.
    """

    def __init__(self, emit: Callable[[str], None], *, max_chars: int = DEFAULT_MAX_SENTENCE_CHARS, on_text: Callable[[], None] | None = None) -> None:
        self._emit = emit
        self.on_text = on_text
        self._spoken_raw: list[str] = []
        self._max = max_chars
        self._buffer = ""
        self._reply: list[str] = []
        self._held = False
        #: Sentences emitted so far in this turn.
        self.emitted = 0
        #: Every sentence this turn said, across attempts. A retry drops the unfinished
        #: buffer of the earlier attempt, but words already heard stay heard.
        self.spoken: list[str] = []
        self.last_text = ""

    def begin(self) -> None:
        """Start one model reply (also called before a retried request)."""

        # ``spoken`` is deliberately kept: only unspoken text is discarded.
        self._buffer = ""
        self._reply = []
        self._held = False

    def feed(self, delta: str) -> None:
        if self._held or not delta:
            return
        if self.on_text is not None and delta.strip():
            self.on_text()
        self._reply.append(delta)
        self._buffer += delta
        while (cut := _sentence_end(self._buffer, self._max)) is not None:
            sentence, self._buffer = self._buffer[:cut], self._buffer[cut:]
            self._say(sentence)

    def hold(self) -> None:
        """A tool call started: speak nothing more from this reply."""

        self._held = True
        self._buffer = ""

    def end(self) -> None:
        """The reply finished: speak what is left, unless a tool call held it."""

        if not self._held:
            self._say(self._buffer)
        self._buffer = ""
        self.last_text = "" if self._held else "".join(self._reply).strip()

    def unspoken(self, answer: str) -> str:
        """The part of ``answer`` this turn has not already said.

        The committed answer joins every message chunk of the turn, including text spoken
        before a tool call or a retry. Returning the remainder is what keeps those words
        from being said twice; when the answer does not begin with what was said, all of
        it is new and is returned unchanged.
        """

        if same_text(self.last_text, answer):
            return ""
        spoken = "".join(self._spoken_raw)
        if not spoken.strip():
            return answer.strip()
        # Match original chunk text without inserting word boundaries. Whitespace
        # may be normalized when the runtime commits its final answer.
        pattern = r"\s*".join(re.escape(char) for char in spoken if not char.isspace())
        match = re.match(r"\s*" + pattern + r"\s*", answer)
        return answer[match.end():].strip() if match else answer.strip()

    def _say(self, text: str) -> None:
        raw = text
        text = text.strip()
        if text:
            self._spoken_raw.append(raw)
            self.emitted += 1
            self.spoken.append(text)
            self._emit(text)


def _sentence_end(buffer: str, max_chars: int) -> int | None:
    """Index just past the first complete sentence in ``buffer``, or None."""

    for index, char in enumerate(buffer):
        if char in _HARD_ENDS:
            return index + 1
        # A full stop ends a sentence only before whitespace: "3.14" and "e.g." do not.
        if char == "." and index + 1 < len(buffer) and buffer[index + 1].isspace():
            return index + 1
    if len(buffer) < max_chars:
        return None
    window = buffer[:max_chars]
    for cut in (max(window.rfind(mark) for mark in _COMMAS), window.rfind(" ")):
        if cut >= max_chars // 2:
            return cut + 1
    return max_chars


def same_text(spoken: str, answer: str) -> bool:
    """Whether ``answer`` is the text already spoken, ignoring whitespace."""

    return bool(spoken) and "".join(spoken.split()) == "".join(answer.split())


_current: ContextVar[SpeechStream | None] = ContextVar("odessy_speech_stream", default=None)


@contextmanager
def speech_scope(stream: SpeechStream | None) -> Iterator[None]:
    """Stream model text into ``stream`` inside the block; ``None`` turns it off."""

    token = _current.set(stream)
    try:
        yield
    finally:
        _current.reset(token)


def current_speech() -> SpeechStream | None:
    return _current.get()
