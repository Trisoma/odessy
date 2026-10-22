"""Compatibility entry point; implementation: installer.components.toolbundle."""
from importlib import import_module
import sys

if __name__ == "__main__":
    raise SystemExit(import_module("installer.components.toolbundle").main())
sys.modules[__name__] = import_module("installer.components.toolbundle")
