"""Compatibility entry point; implementation: installer.operations.maintenance."""
from importlib import import_module
import sys

if __name__ == "__main__":
    raise SystemExit(import_module("installer.operations.maintenance").main())
sys.modules[__name__] = import_module("installer.operations.maintenance")
