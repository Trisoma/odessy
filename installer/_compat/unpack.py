"""Compatibility entry point; implementation: installer.core.unpack."""
from importlib import import_module
import sys

if __name__ == "__main__":
    raise SystemExit(import_module("installer.core.unpack").main())
sys.modules[__name__] = import_module("installer.core.unpack")
