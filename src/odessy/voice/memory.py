"""In-memory conversation history for a voice session.

A voice session asks follow-up questions ("what about the other one?"), which needs
the previous exchanges in the request. That is a *new* thing to send to a model, so it
is bounded, redacted, and never persisted:

* at most six completed exchanges and 8,000 characters, oldest pairs dropped first;
* redacted before storage, so a secret spoken once is not re-sent on every later turn;
* RAM only, cleared on exit, never written to disk;
* interrupted turns are not recorded, because they have no answer to remember.

Carrying forward a tool result or an approval is deliberately *not* done here: prior
approvals and tool-call identifiers belong to the turn that produced them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from odessy.redaction import redact_text
from odessy.runtime.types import ConversationContext, ConversationTurn

DEFAULT_MAX_EXCHANGES = 6
DEFAULT_MAX_CHARS = 8_000


@dataclass
class ConversationMemory:
    max_exchanges: int = DEFAULT_MAX_EXCHANGES
    max_chars: int = DEFAULT_MAX_CHARS
    _turns: list[ConversationTurn] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.max_exchanges < 0 or self.max_chars < 1:
            raise ValueError("max_exchanges must be non-negative and max_chars positive")

    def add(self, user_text: str, assistant_text: str) -> None:
        """Record one completed exchange, redacted and within the bounds."""

        user = redact_text(user_text).strip()
        assistant = redact_text(assistant_text).strip()
        if not user and not assistant:
            return
        self._turns.append(ConversationTurn(user=user, assistant=assistant))
        self._trim()

    def clear(self) -> None:
        self._turns.clear()

    @property
    def turns(self) -> tuple[ConversationTurn, ...]:
        return tuple(self._turns)

    def context(self) -> ConversationContext:
        return ConversationContext(turns=tuple(self._turns))

    def _trim(self) -> None:
        while len(self._turns) > self.max_exchanges:
            self._turns.pop(0)
        # A single exchange can exceed the whole budget; it is truncated rather than
        # kept whole, because "within 8,000 characters" is the promise being kept.
        while self._turns and self._size() > self.max_chars:
            if len(self._turns) > 1:
                self._turns.pop(0)
                continue
            turn = self._turns[0]
            excess = self._size() - self.max_chars
            assistant = turn.assistant
            if len(assistant) >= excess:
                self._turns[0] = ConversationTurn(
                    user=turn.user, assistant=assistant[: max(0, len(assistant) - excess)]
                )
            else:
                self._turns[0] = ConversationTurn(
                    user=turn.user[: max(0, len(turn.user) - (excess - len(assistant)))],
                    assistant="",
                )
            if not self._turns[0].user and not self._turns[0].assistant:
                self._turns.pop(0)
            break

    def _size(self) -> int:
        return sum(len(turn.user) + len(turn.assistant) for turn in self._turns)
