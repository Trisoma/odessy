"""Compatibility entry point; implementation: installer.build.build_exe."""
from importlib import import_module
import sys

if __name__ == "__main__":
    raise SystemExit(import_module("installer.build.build_exe").main())
sys.modules[__name__] = import_module("installer.build.build_exe")
