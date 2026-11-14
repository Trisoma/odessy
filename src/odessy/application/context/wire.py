"""Small shared helpers for Wave 2 routes (error shape and argument parsing)."""
from __future__ import annotations

from ..conversations.store import Invalid, StorageError


def detail(exc: StorageError):
    return {'code': exc.code, 'message': str(exc)}


def int_arg(value, name, default, *, minimum=0, maximum=None):
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        raise Invalid(f'{name} must be an integer')
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise Invalid(f'{name} must be an integer') from None
    if parsed < minimum or (maximum is not None and parsed > maximum):
        raise Invalid(f'{name} is out of range')
    return parsed


def only(data, allowed):
    unknown = set(data) - allowed
    if unknown:
        raise Invalid(f'Unknown fields: {", ".join(sorted(unknown))}')
