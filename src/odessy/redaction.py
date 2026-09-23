"""Secret redaction at Odessy's persistence and model-facing boundaries.

Extends the conventions already in `command_bridge.py` rather than replacing
them: the secret-name vocabulary below is the same one that screens the
sanitized subprocess environment, moved here so that *one* vocabulary answers
both questions ("may this variable reach a child process?" and "may this value
reach the model or a file?").

**This is not a complete security guarantee.** Detection is name-based plus a
small set of well-known token shapes. A secret printed in a form that matches
neither -- an opaque value in an arbitrary variable, a credential echoed by a
tool that does not name it -- is not caught. It is a best-effort boundary that
raises the cost of accidental leakage, not a proof of safety. Anything stronger
needs the credential-as-reference policy, where the value never enters the
process in the first place.

The ordering rule that matters: **redact before persisting, not before sending.**
Redacting on the way out of the runtime leaves the secret on disk in the spill
file; by then it is too late to recall.
"""

from __future__ import annotations

import re

#: Placeholder written in place of a redacted value. Keeps the name so the model
#: can still reason about *which* credential was involved, which is the same
#: "a key name is safe to print, a value is not" line DeepSeek Harness draws.
REDACTED = "<redacted>"

#: Variable / field names that are treated as credentials. Matched as a
#: case-insensitive substring, so `MY_APP_API_KEY`, `apiKey`, and `apikey` are
#: all covered by `API_KEY`.
SECRET_NAME_TERMS = frozenset(
    {
        "APIKEY",
        "API_KEY",
        "AWS_",
        "AZURE_",
        "CLIENT_SECRET",
        "CREDENTIAL",
        "GITHUB_TOKEN",
        "HF_TOKEN",
        "HUGGINGFACE",
        "OPENAI_",
        "PASSWD",
        "PASSWORD",
        "PASSPHRASE",
        "PRIVATE_KEY",
        "PRIVATE",
        "SECRET",
        "TOKEN",
    }
)

#: Well-known credential shapes, caught even when the surrounding name is
#: innocuous. Deliberately a short, high-confidence list: a loose pattern here
#: would corrupt legitimate output.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abpsr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

#: `NAME=value`, `NAME: value`, `"name": "value"`, optionally quoted. The value
#: must be at least 6 characters, which keeps short non-secret assignments
#: (`TOKEN=1`, `DEBUG=on`) untouched.
#:
#: Both quantifiers are BOUNDED, and that is load-bearing rather than cosmetic.
#: With an unbounded `name` group, a long run of word characters containing no
#: separator forces the engine to retry every split point at every offset --
#: quadratic behaviour that made `redact_text` take 33 s on a 50 000-character
#: line of plain text. Terminal output produces exactly that shape (base64
#: blobs, progress bars, long log lines), so this is a hot-path correctness
#: constraint. A bounded repeat is linear: at most 64 attempts per offset.
#:
#: The `value` group is also bounded, which additionally caps how much text a
#: single match can claim.
_ASSIGNMENT = re.compile(
    r"""(?P<nq>["']?)(?P<name>[A-Za-z_][A-Za-z0-9_]{0,63})(?P=nq)"""
    r"""\s*(?P<sep>=|:)\s*"""
    r"""(?P<vq>["']?)(?P<value>[^\s"',;]{6,4096})(?P=vq)"""
)

_MIN_SECRET_VALUE_LEN = 6


def looks_like_secret_name(name: str) -> bool:
    """Whether a variable or field name is treated as credential-bearing."""

    upper = name.upper()
    return any(term in upper for term in SECRET_NAME_TERMS)


def redact_text(text: str) -> str:
    """Return ``text`` with recognised credential values replaced.

    Idempotent: already-redacted output is unchanged, so applying it twice is
    safe (which matters because it is applied at more than one boundary).
    """

    if not text:
        return text

    def _replace_assignment(match: re.Match[str]) -> str:
        name = match.group("name")
        value = match.group("value")
        if value == REDACTED:
            return match.group(0)
        if not looks_like_secret_name(name):
            return match.group(0)
        if len(value) < _MIN_SECRET_VALUE_LEN:
            return match.group(0)
        return f"{match.group('nq')}{name}{match.group('nq')}{match.group('sep')} {REDACTED}"

    redacted = _ASSIGNMENT.sub(_replace_assignment, text)
    for pattern in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def redact_for_log(text: str) -> str:
    """Redact a value on its way into a debug log or an error message.

    Separate name from `redact_text` so the two boundaries can diverge later if
    logging ever needs to be stricter than model-facing text.
    """

    return redact_text(text)
