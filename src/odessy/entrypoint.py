"""Minimal bootstrap: setup must work before the agent runtime is configured."""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ['setup']:
        from odessy.setup.cli import main as setup_main
        raise SystemExit(setup_main(arguments[1:]))
    from odessy.cli import main as runtime_main
    runtime_main(arguments)
