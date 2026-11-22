"""Conservative, dependency-free token estimation for context budgeting.

This is a *measuring* tool, not a tokenizer and not an authority. It is used only
to decide whether a request is likely to exceed a model's context window, so it
errs on the high side: it is better to refuse one borderline request than to send
one that the provider truncates or rejects.

Design rules (matching the rest of Odessy's context plumbing):

* deterministic and pure -- no model, network, process, file, or clock access;
* counts characters only, with a conservative chars-per-token ratio and a small
  fixed per-message overhead (role, separators, tool scaffolding);
* never claims to equal a provider's real token count. Providers report the real
  number after the fact; this estimate exists only for the pre-request guard.
"""

from __future__ import annotations

from math import ceil
from typing import Any, Iterable, Mapping

#: Conservative ratio: assume fewer characters per token than the ~4 often cited
#: for English prose. CJK and code tokenize denser, so 3 is a safer over-estimate.
DEFAULT_CHARS_PER_TOKEN = 3
#: Fixed overhead per message for role labels, separators and message scaffolding.
MESSAGE_OVERHEAD_TOKENS = 4


def estimate_tokens(text: str, *, chars_per_token: int = DEFAULT_CHARS_PER_TOKEN) -> int:
    """Estimate the token cost of ``text``, rounded up and biased high.

    Non-strings count as zero. The ratio is floored at 1 so a bad configuration
    cannot divide by zero or produce a nonsensical under-estimate.
    """

    if not isinstance(text, str) or not text:
        return 0
    ratio = max(1, int(chars_per_token))
    return ceil(len(text) / ratio)


def _message_text(message: Any) -> str:
    """Flatten one chat message to the text that is actually billed as content.

    Handles both the OpenAI shape (``content`` is str or a list of parts) and the
    Anthropic shape (list of ``{type, text}`` blocks, incl. tool_result). Only text
    fields are read; no structure is interpreted as instructions.
    """

    if not isinstance(message, Mapping):
        return ''
    content = message.get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                # Anthropic tool_result blocks carry their payload in ``content``
                # (str or nested blocks); text/image blocks use ``text``. Count
                # both so a tool result is not under-estimated.
                for value in (block.get('text'), block.get('content')):
                    if isinstance(value, str):
                        parts.append(value)
                    elif isinstance(value, (list, tuple)):
                        for inner in value:
                            if isinstance(inner, Mapping) and isinstance(inner.get('text'), str):
                                parts.append(inner['text'])
                            elif isinstance(inner, str):
                                parts.append(inner)
        return '\n'.join(parts)
    return ''


def estimate_messages(
    messages: Iterable[Any], *, chars_per_token: int = DEFAULT_CHARS_PER_TOKEN
) -> int:
    """Estimate the total token cost of a prepared message list.

    Adds a fixed per-message overhead so that many small messages are not
    under-counted during the pre-request guard.
    """

    total = 0
    count = 0
    for message in messages:
        count += 1
        total += estimate_tokens(_message_text(message), chars_per_token=chars_per_token)
    return total + count * MESSAGE_OVERHEAD_TOKENS
