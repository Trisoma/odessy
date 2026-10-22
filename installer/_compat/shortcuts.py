"""Compatibility entry point; implementation: installer.windows.shortcuts."""
from importlib import import_module
import sys

if __name__ == "__main__":
    raise SystemExit(import_module("installer.windows.shortcuts").main())
sys.modules[__name__] = import_module("installer.windows.shortcuts")
