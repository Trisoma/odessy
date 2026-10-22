"""Compatibility entry point; implementation: installer.operations.component_repair."""
from importlib import import_module
import sys

if __name__ == "__main__":
    raise SystemExit(import_module("installer.operations.component_repair").main())
sys.modules[__name__] = import_module("installer.operations.component_repair")
