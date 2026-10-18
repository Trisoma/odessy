from __future__ import annotations

from typing import Any


def loads_toml(text: str) -> dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10 uses the tomli backport.
        import tomli as tomllib
    return tomllib.loads(text)
