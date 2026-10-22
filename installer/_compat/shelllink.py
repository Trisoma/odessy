"""Compatibility entry point; implementation: installer.windows.shelllink."""
from importlib import import_module
import sys

if __name__ == "__main__":
    raise SystemExit(import_module("installer.windows.shelllink").main())
sys.modules[__name__] = import_module("installer.windows.shelllink")
