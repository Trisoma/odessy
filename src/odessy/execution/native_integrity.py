"""Integrity checks for packaged native dependencies.

Two different assets need two different checks, and mixing them up gives false
confidence:

- *Pinned prebuilt binaries* (node-pty's ``conpty.node`` and friends) ship inside
  the package with hashes that are fixed upstream. We compare bytes against a
  receipt shipped in source control; a mismatch means the packaged binary was
  replaced.
- *Locally built artifacts* (the windows-acl ``runner.js``) are produced by
  ``tsc`` from source on each install, so their bytes are not stable across Node
  or TypeScript versions. Hashing those bytes against a checked-in value would
  break on every legitimate rebuild. For these we anchor the supply chain
  instead: the npm dependency version must match the lockfile, and we expose the
  ``verify_sha256_file`` primitive for callers that do have a stable receipt.

Both checks are pure and side-effect free. A failure is reported to the caller,
which decides whether to fail closed (the confinement providers do).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

__all__ = ["verify_sha256_file", "verify_receipt_hashes", "locked_package_version", "verify_package_version"]


def verify_sha256_file(path: Path, expected: str) -> bool:
    """Return whether ``path`` exists, is a regular file and hashes to ``expected``.

    ``expected`` is a lowercase hex SHA-256 digest. Any read/parse problem is a
    mismatch, never an exception: callers treat ``False`` as "do not trust this
    binary" and degrade/fail closed.
    """
    if not isinstance(expected, str) or len(expected) != 64:
        return False
    try:
        if not path.is_file():
            return False
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, ValueError):
        return False
    return actual.lower() == expected.lower()


def verify_receipt_hashes(entries, base: Path) -> tuple[bool, tuple[str, ...]]:
    """Verify a ConPTY-style receipt's ``native_sha256`` entries.

    ``entries`` is an iterable of ``{"path": ..., "sha256": ...}`` mappings with
    paths relative to ``base``. Returns ``(all_ok, failed_paths)``. Malformed
    entries count as failures so a truncated/edited receipt cannot silently pass.
    """
    failures: list[str] = []
    seen = 0
    for entry in entries or ():
        seen += 1
        if not isinstance(entry, dict):
            failures.append("<malformed entry>")
            continue
        rel, digest = entry.get("path"), entry.get("sha256")
        if not isinstance(rel, str) or not isinstance(digest, str):
            failures.append(str(rel or "<malformed entry>"))
            continue
        if not verify_sha256_file(base / rel, digest):
            failures.append(rel)
    # An empty receipt verifies nothing: report it as unusable rather than a
    # vacuous success, so a packaging mistake cannot look like a clean check.
    if seen == 0:
        failures.append("<empty receipt>")
    return (not failures), tuple(failures)


def locked_package_version(node_modules_dir: Path, package: str) -> str | None:
    """Read an installed npm package's version from its ``package.json``.

    Returns ``None`` when the package or its manifest is missing/unreadable.
    Callers compare this with the version pinned in the lockfile.
    """
    try:
        manifest = json.loads((node_modules_dir / package / "package.json").read_text("utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    version = manifest.get("version") if isinstance(manifest, dict) else None
    return version if isinstance(version, str) else None


def verify_package_version(node_modules_dir: Path, package: str, expected: str) -> tuple[bool, str]:
    """Return ``(ok, detail)`` for an installed package's version anchor.

    This anchors the supply chain for locally built artifacts: a locally built
    ``runner.js`` is only trusted when its FFI dependency is the pinned version.
    It is not a byte-integrity check of the built artifact itself.
    """
    actual = locked_package_version(node_modules_dir, package)
    if actual is None:
        return False, f"{package} manifest is missing or unreadable"
    if actual != expected:
        return False, f"{package} version {actual} does not match pinned {expected}"
    return True, f"{package} {actual}"
