"""Compatibility entry point; implementation: installer.core.download."""
from importlib import import_module
import sys

if __name__ == "__main__":
    raise SystemExit(import_module("installer.core.download").main())
sys.modules[__name__] = import_module("installer.core.download")
