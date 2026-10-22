"""Compatibility entry point; implementation: installer.windows.session_process."""
from importlib import import_module
import sys

if __name__ == "__main__":
    raise SystemExit(import_module("installer.windows.session_process").main())
sys.modules[__name__] = import_module("installer.windows.session_process")
