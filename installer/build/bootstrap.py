"""Stage the EXE's verified prebuilt resource in a stable per-user location."""
from __future__ import annotations
import json
import os
from pathlib import Path
from installer.components import application
from installer.core.download import _reject_link
from installer.core.process import owned_directory


def bundled_tools(repo: Path, local: Path):
    bundle = repo / 'bundled-tools'
    archive, metadata = application.validate_bundle(bundle, repo)
    target = local / 'Odessy/installer-resources' / metadata['sha256']
    _reject_link(target)
    if target.exists():
        application.validate_bundle(target, repo)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with owned_directory(target) as output:
            (output / 'tool-server.zip').write_bytes(archive.read_bytes())
            (output / 'tool-server.resources.json').write_bytes((bundle / 'tool-server.resources.json').read_bytes())
            application.validate_bundle(output, repo)
    except ValueError:
        # A concurrent initializer may have completed; never replace its files.
        application.validate_bundle(target, repo)
    return target
