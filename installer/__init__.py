"""Standalone installer support; does not import the Odessy runtime."""

# Preserve historical imports and python -m commands.
from pathlib import Path as _Path
__path__.append(str(_Path(__file__).with_name("_compat")))
