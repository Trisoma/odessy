"""Whole-response intent phrases; model output never decides authorization."""
import re

CANCEL_PHRASES = frozenset({
    'stop', 'cancel', 'never mind', 'nevermind',
    'stop this', 'stop that', 'cancel this', 'cancel that',
    'please stop', 'please cancel', 'stop please', 'cancel please',
    'stop this please', 'cancel that please',
})
DENY_PHRASES = frozenset({
    'deny', 'no', 'reject', 'i deny', 'i reject',
    "don't do that", 'do not do that', 'no thanks', 'no thank you',
    'deny this', 'reject this', 'please deny', 'please reject',
    'please deny this', 'please reject this',
})


def normalize_answer(text: str) -> str:
    # Keep apostrophes (including typographic ones) so negations stay intact.
    return ' '.join(re.findall(r"[\w']+", text.casefold().replace('\u2019', "'")))


def is_cancel(text: str) -> bool:
    return normalize_answer(text) in CANCEL_PHRASES
