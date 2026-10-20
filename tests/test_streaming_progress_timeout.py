"""Streaming progress-timeout tests.

The Ollama chat call now streams NDJSON and the timeout measures **silence
between chunks**, not total generation time. These tests cover the accumulation
logic and the configuration surface directly. The socket-level timing itself is
inherited from ``urlopen``'s per-read timeout and needs a live server, so it is
not unit-tested here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from odessy.ollama_model import (
    DEFAULT_PROGRESS_TIMEOUT_SECONDS,
    PROGRESS_TIMEOUT_ENV,
    ModelTurnDiagnostics,
    _accumulate_stream,
    _default_progress_timeout,
)
from odessy.lfm_model import LlamaCppLFMModel


def stream(*chunks: dict[str, Any]) -> list[bytes]:
    """Render chunks as the NDJSON lines Ollama would send."""

    return [(json.dumps(chunk) + "\n").encode("utf-8") for chunk in chunks]


def content_chunk(text: str) -> dict[str, Any]:
    return {"message": {"role": "assistant", "content": text}, "done": False}


def test_accumulates_multiple_content_chunks() -> None:
    """Text arrives as many chunks and must be joined in order."""

    result = _accumulate_stream(
        stream(
            content_chunk("The current "),
            content_chunk("time is "),
            content_chunk("noon."),
            {"message": {"role": "assistant", "content": ""}, "done": True},
        )
    )

    assert result["message"]["content"] == "The current time is noon."
    assert result["done"] is True


def test_accumulates_streamed_tool_call() -> None:
    """A tool call delivered mid-stream must survive accumulation intact."""

    result = _accumulate_stream(
        stream(
            {"message": {"role": "assistant", "content": ""}, "done": False},
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "terminal.run",
                                "arguments": {"command": "pwd"},
                            }
                        }
                    ],
                },
                "done": False,
            },
            {"message": {"role": "assistant", "content": ""}, "done": True},
        )
    )

    calls = result["message"]["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "terminal.run"
    assert calls[0]["function"]["arguments"] == {"command": "pwd"}


def test_accumulates_thinking_and_terminal_metadata() -> None:
    """Thinking text and the terminal chunk's stats are both preserved."""

    result = _accumulate_stream(
        stream(
            {"message": {"role": "assistant", "content": "", "thinking": "step one "}},
            {"message": {"role": "assistant", "content": "", "thinking": "step two"}},
            content_chunk("Done."),
            {
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                "eval_count": 42,
                "prompt_eval_count": 948,
            },
        )
    )

    assert result["message"]["thinking"] == "step one step two"
    assert result["message"]["content"] == "Done."
    assert result["done_reason"] == "stop"
    assert result["eval_count"] == 42
    assert result["prompt_eval_count"] == 948


def test_stops_at_done_and_ignores_trailing_chunks() -> None:
    """Accumulation terminates at the done chunk."""

    result = _accumulate_stream(
        stream(
            content_chunk("Answer."),
            {"message": {"role": "assistant", "content": ""}, "done": True},
            content_chunk("TRAILING SHOULD NOT APPEAR"),
        )
    )

    assert result["message"]["content"] == "Answer."


def test_malformed_and_blank_lines_are_skipped() -> None:
    """A bad line must not abort an otherwise healthy stream."""

    raw = [
        b"\n",
        b"not json at all\n",
        (json.dumps(content_chunk("Kept.")) + "\n").encode("utf-8"),
        b'{"message": {"role": "assistant", "content": ""}, "done": true}\n',
    ]

    result = _accumulate_stream(raw)

    assert result["message"]["content"] == "Kept."


def test_many_timely_chunks_do_not_time_out() -> None:
    """Elapsed wall time across the stream does not itself trigger a timeout.

    The accumulator holds no total-time budget: only the per-read socket timeout
    can raise, and every iteration here returns immediately. A long generation
    with a steady stream of chunks therefore completes regardless of how many
    chunks it contains.
    """

    chunks = [content_chunk(f"tok{i} ") for i in range(500)]
    chunks.append({"message": {"role": "assistant", "content": ""}, "done": True})

    result = _accumulate_stream(stream(*chunks))

    assert result["message"]["content"].count("tok") == 500


def test_progress_timeout_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PROGRESS_TIMEOUT_ENV, raising=False)

    assert _default_progress_timeout() == DEFAULT_PROGRESS_TIMEOUT_SECONDS


def test_progress_timeout_honours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROGRESS_TIMEOUT_ENV, "45")

    assert _default_progress_timeout() == 45


@pytest.mark.parametrize("bad", ["", "   ", "not-a-number", "0", "-5"])
def test_invalid_progress_timeout_falls_back(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """A malformed or non-positive value must degrade, not crash."""

    monkeypatch.setenv(PROGRESS_TIMEOUT_ENV, bad)

    assert _default_progress_timeout() == DEFAULT_PROGRESS_TIMEOUT_SECONDS


def test_adapter_picks_up_configured_progress_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter reads the configured value when none is passed explicitly."""

    monkeypatch.setenv(PROGRESS_TIMEOUT_ENV, "77")

    model = LlamaCppLFMModel()

    assert model.timeout == 77


def test_explicit_timeout_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicitly passed timeout is not overridden by the environment."""

    monkeypatch.setenv(PROGRESS_TIMEOUT_ENV, "77")

    model = LlamaCppLFMModel(timeout=15)

    assert model.timeout == 15
