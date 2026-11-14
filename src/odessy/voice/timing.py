"""Measured voice phases, each reported on its own (prompt 13).

A voice turn spends its time in four different places, and adding them into one
number hides which one to fix:

* **capture** — from the recording request to the end of the utterance;
* **recognition** — from the end of the utterance to the first transcript;
* **first text** — from the transcript to the first text the runtime produced;
  this interval contains the model and network work, so it is *not* bridge
  latency and must never be labelled as such;
* **first audio** — from that first text to the first playback request. This is
  a software boundary, not an observation of the device's first audible sample.

Every mark is a monotonic timestamp taken by the code that owns that step. A phase
with no mark stays ``None`` instead of becoming a zero, so "not measured" and
"measured as fast" cannot be confused.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

#: Where the first text came from. A delta is the engine streaming its reply; a
#: settled answer is the turn's committed result, which is all the legacy engine and
#: a non-streaming turn ever produce.
DELTA_SOURCE = "runtime-delta"
SETTLED_SOURCE = "settled-answer"


def _ms(earlier: float | None, later: float | None) -> float | None:
    if earlier is None or later is None:
        return None
    return round((later - earlier) * 1000, 3)


@dataclass
class VoiceTiming:
    """One voice turn's phase marks. Mutable, reused per turn, never persisted."""

    generation: int = 0
    turn: str = ""
    started: float | None = None
    capture_end: float | None = None
    stt_done: float | None = None
    first_text: float | None = None
    first_text_source: str = ""
    first_audio: float | None = None
    phases: list[str] = field(default_factory=list)

    def begin(self, turn: str = "") -> "VoiceTiming":
        """Start a fresh turn: every previous mark is cleared."""
        self.generation += 1
        self.turn = turn
        self.started = time.monotonic()
        self.capture_end = None
        self.stt_done = None
        self.first_text = None
        self.first_text_source = ""
        self.first_audio = None
        self.phases = []
        return self

    @property
    def active(self) -> bool:
        return self.started is not None

    @property
    def has_first_text(self) -> bool:
        return self.first_text is not None

    def _mark(self, name: str) -> bool:
        if name in self.phases:
            return False
        self.phases.append(name)
        return True

    def mark_capture_end(self) -> bool:
        if not self.active or self.capture_end is not None:
            return False
        self.capture_end = time.monotonic()
        return self._mark("capture")

    def mark_stt_done(self) -> bool:
        if not self.active or self.stt_done is not None:
            return False
        self.stt_done = time.monotonic()
        return self._mark("stt")

    def mark_first_text(self, source: str = DELTA_SOURCE) -> bool:
        """The first text this turn produced. Only the first one is recorded.

        Nothing is invented here: a turn that never captured audio keeps ``capture_end``
        and ``stt_done`` unmeasured rather than stamping them at this moment.
        """
        if not self.active or self.first_text is not None:
            return False
        self.first_text = time.monotonic()
        self.first_text_source = source
        return self._mark("first_text")

    def mark_first_audio(self) -> bool:
        """Playback was requested. Device-level audible onset is not measured."""
        if not self.active or self.first_audio is not None:
            return False
        self.first_audio = time.monotonic()
        return self._mark("first_audio")

    def clear_first_audio(self) -> bool:
        """Playback did not start after all: withdraw the mark instead of claiming it.

        The mark is taken when playback is requested, because a first sample cannot be
        observed from outside the device. A player that then raises never produced sound,
        so leaving the mark would report an audible output that never happened.
        """
        if self.first_audio is None:
            return False
        self.first_audio = None
        self.phases = [name for name in self.phases if name != "first_audio"]
        return True

    def settle(self, *, settled: bool) -> dict[str, object]:
        """Final snapshot for a turn, and whether its answer was its first text.

        Only a turn that produced a committed answer can call that answer its first text.
        A failed or cancelled turn has no answer, so the phase stays unmeasured rather
        than being labelled ``settled-answer``.
        """
        if settled and not self.has_first_text:
            self.mark_first_text(SETTLED_SOURCE)
        return self.public()

    def public(self) -> dict[str, object]:
        """Safe snapshot: durations in milliseconds and the source of the text.

        ``not_measured`` names every phase without a mark, so a consumer cannot read
        a missing number as a fast one. No transcript, path or audio is included.
        """
        # A phase whose predecessor never happened is measured from the last point that
        # did, so a text-only turn still reports first text without pretending it had a
        # capture or a recognition step.
        text_from = self.stt_done or self.capture_end or self.started
        return {
            "turn": self.turn,
            "capture_ms": _ms(self.started, self.capture_end),
            "stt_ms": _ms(self.capture_end or self.started, self.stt_done),
            "first_text_ms": _ms(text_from, self.first_text),
            "first_text_source": self.first_text_source or None,
            "first_audio_ms": _ms(self.first_text, self.first_audio),
            "first_audio_measurement": "playback-request",
            "audio_from_stt_ms": _ms(self.stt_done, self.first_audio),
            # Model and network latency is inside first_text_ms; naming it here keeps a
            # reader from mistaking it for transport/bridge time.
            "first_text_includes_model_and_network": True,
            "not_measured": [
                name for name in ("capture", "stt", "first_text", "first_audio")
                if name not in self.phases
            ],
        }
