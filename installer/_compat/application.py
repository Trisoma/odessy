"""Compatibility entry point; implementation: installer.components.application."""
from importlib import import_module
import sys

if __name__ == "__main__":
    raise SystemExit(import_module("installer.components.application").main())
sys.modules[__name__] = import_module("installer.components.application")
