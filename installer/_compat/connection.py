"""Compatibility entry point; implementation: installer.components.connection."""
from importlib import import_module
import sys

if __name__ == "__main__":
    raise SystemExit(import_module("installer.components.connection").main())
sys.modules[__name__] = import_module("installer.components.connection")
