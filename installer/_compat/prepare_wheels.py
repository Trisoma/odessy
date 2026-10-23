"""Compatibility entry point; implementation: installer.build.prepare_wheels."""
from importlib import import_module
import sys

if __name__ == "__main__":
    raise SystemExit(import_module("installer.build.prepare_wheels").main())
sys.modules[__name__] = import_module("installer.build.prepare_wheels")
