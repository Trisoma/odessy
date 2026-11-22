"""Estimate input tokens without changing the provider payload."""

from __future__ import annotations

import json
from math import ceil
from typing import Any, Iterable, Mapping

DEFAULT_CHARS_PER_TOKEN = 3
MESSAGE_OVERHEAD_TOKENS = 4


def estimate_tokens(text: str, *, chars_per_token: int = DEFAULT_CHARS_PER_TOKEN) -> int:
    """Convert a character count to a rough token estimate, rounding up."""
    if not isinstance(text, str) or not text:
        return 0
    return ceil(len(text) / max(1, int(chars_per_token)))


def _sdk_value(value: Any) -> Any:
    # Claude returns SDK objects for assistant blocks, including tool inputs.
    dump = getattr(value, 'model_dump', None)
    if dump is not None:
        return dump(mode='json', exclude_none=True)
    raise TypeError(f'unsupported context value: {type(value).__name__}')


def _estimate_value(value: Any, chars_per_token: int) -> int:
    if isinstance(value, str):
        return estimate_tokens(value, chars_per_token=chars_per_token)
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=_sdk_value)
    return estimate_tokens(text, chars_per_token=chars_per_token)


def estimate_messages(
    messages: Iterable[Any], *, chars_per_token: int = DEFAULT_CHARS_PER_TOKEN
) -> int:
    """Estimate messages including structured calls, results, and reasoning blocks."""
    return sum(_estimate_value(message, chars_per_token) + MESSAGE_OVERHEAD_TOKENS
               for message in messages)


def estimate_cloud_input(payload: Mapping[str, Any], *, chars_per_token: int) -> int:
    """Count the input fields in an OpenAI-compatible or Claude request."""
    total = estimate_messages(payload.get('messages', ()), chars_per_token=chars_per_token)
    for key in ('system', 'tools'):
        if payload.get(key):
            total += _estimate_value(payload[key], chars_per_token)
    return total
