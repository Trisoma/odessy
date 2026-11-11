"""The reviewed Odessy profile overlay, as source text rather than a shipped file type.

Why not a ``.yml`` beside this module: ``installer/components/application.py`` admits only
``.py``, ``.json``, ``.toml``, ``.txt`` and ``.md`` anywhere under ``src/odessy``, and that
allowlist is a reviewed packaging decision (prompt 14 owns changing it). Keeping the overlay
as a string constant means the content is hashed with the rest of the source, no new asset
type is shipped, and nothing about the installer policy changes here.

The text is materialised into the Odessy-owned Harness home before launch, because the
runtime takes ``--patch <file>``. It is written only when the content differs, so repeated
sessions do not rewrite it.

What it does: disables every model-facing tool row, so a turn cannot execute anything until
prompts 07-10 implement a tool surface deliberately. Verified against the pinned binary by
``experiments/restructure/harness_compatibility/verify_no_tools.py`` (roster empty) and by
``smoke_p03.py`` (the provider receives no tools). A row rename at a new pin must re-run both,
because disabling a row that no longer exists is silently a no-op.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

NARROW_PATCH_NAME = "odessy_narrow.patch.yml"

NARROW_PATCH_TEXT = """\
# Odessy narrow profile: no model-facing tool executes until prompts 07-10.
# Generated from odessy/runtime_backends/harness/profile.py; edit that constant, not this file.
- id: tool-pwsh
  disabled: true
- id: tool-bash
  disabled: true
- id: tool-fs
  disabled: true
- id: tool-fs-search
  disabled: true
- id: tool-web
  disabled: true
- id: tool-skill
  disabled: true
- id: tool-subagent
  disabled: true
- id: tool-subagent-control
  disabled: true
- id: tool-subagent-list-agents
  disabled: true
- id: tool-subagent-fork
  disabled: true
- id: tool-workflow
  disabled: true
- id: tool-todo
  disabled: true
- id: tool-goal
  disabled: true
- id: tool-jobs
  disabled: true
- id: tool-working-directory
  disabled: true
- id: plan-mode
  disabled: true
- id: mcp-resources
  disabled: true
"""


def materialize_narrow_patch(home: Path) -> Path:
    """Write the reviewed overlay into the Harness home and return its path.

    ``home`` must be the Odessy-owned directory that is also handed to the child as
    ``DSH_HOME``; nothing outside it is written.
    """
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    target = home / NARROW_PATCH_NAME
    text = NARROW_PATCH_TEXT
    try:
        if target.read_text(encoding="utf-8") == text:
            return target
    except OSError:
        pass
    fd, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".writing", dir=home)
    scratch = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(scratch, target)
    finally:
        scratch.unlink(missing_ok=True)
    return target
