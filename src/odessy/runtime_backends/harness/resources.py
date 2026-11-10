"""Production inputs of the Harness engine that are not Python code.

Two kinds of input the engine needs are data, and both belong to this package rather than
to an experiment tree or a developer checkout:

``harness-artifacts.json``
    The declared runtime distribution, the SDK (declared because it must not drift, not
    because it is imported), the plugin closure that ships with the package, the platform
    requirements and the licences. :mod:`odessy.runtime_backends.harness.pin` derives its
    enforced constants from this file, so the executable digest that a launch verifies and
    the digest an installer reads are one fact.

``bundle/``
    The plugin bundles the engine composes. They are plain files (a ``package.json`` with a
    ``dsh.bundle.patch`` entry, the patch, and the module), so they are declared as package
    data in ``pyproject.toml`` and travel inside the wheel.

Resolution is package-relative, with **no discovery and no fallback**: no environment
variable, no ``.harness-spike`` folder, no sibling checkout, no source ``PYTHONPATH``. An
installed application resolves exactly the files it shipped with. A file that is missing
or whose digest disagrees with the declaration is reported as such (:func:`bundle_status`)
rather than silently replaced by another copy — the point of declaring digests is to make
"the plugin changed underneath us" visible.

The declared bundle is *optional* at runtime: without it the engine still creates sessions
and settles text, and only the live-delta and clarification capabilities degrade. That is
why a missing bundle is a diagnostic here, not a launch refusal; a missing *inventory* is
different, because the pin itself is read from it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from odessy.runtime_backends.contracts import RuntimeUnavailable

ENGINE = "harness"

#: Declared inventory, shipped next to this module.
ARTIFACTS_NAME = "harness-artifacts.json"
#: Directory holding the plugin bundles that ship with the package.
BUNDLE_DIRECTORY = "bundle"
#: The one bundle this engine composes today.
STREAM_BRIDGE = "odessy-stream-bridge"

#: Keys a usable inventory must carry. Not a schema engine: the inventory is small, reviewed
#: data, and a missing top-level section means the file is not the one this code expects.
_REQUIRED_SECTIONS = ("runtime", "sdk", "plugin_closure", "requirements")

_inventory_cache: Mapping[str, Any] | None = None


def package_root() -> Path:
    """The directory this package was imported from (source tree or installed wheel)."""

    return Path(__file__).resolve().parent


def artifacts_path() -> Path:
    """Where the declared inventory is expected to be found."""

    return package_root() / ARTIFACTS_NAME


def _read_inventory() -> dict[str, Any]:
    path = artifacts_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeUnavailable(
            ENGINE, "the Harness artifact inventory is missing",
            detail=f"{path}: {exc}",
        ) from exc
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeUnavailable(
            ENGINE, "the Harness artifact inventory is not valid JSON",
            detail=f"{path}: {exc}",
        ) from exc
    if not isinstance(loaded, dict):
        raise RuntimeUnavailable(
            ENGINE, "the Harness artifact inventory is not an object", detail=str(path),
        )
    missing = [name for name in _REQUIRED_SECTIONS if not isinstance(loaded.get(name), dict)]
    if missing:
        raise RuntimeUnavailable(
            ENGINE, "the Harness artifact inventory is incomplete",
            detail=f"{path} is missing {', '.join(missing)}",
        )
    return loaded


def artifact_inventory() -> Mapping[str, Any]:
    """The declared inventory, parsed once.

    Read-only by convention: callers get the mapping, not a copy, because it is module-level
    reviewed data that nothing should mutate. ``pin`` reads its constants from here, so a
    malformed file refuses at import rather than at the first launch.
    """

    global _inventory_cache
    if _inventory_cache is None:
        _inventory_cache = _read_inventory()
    return _inventory_cache


def bundle_root(name: str = STREAM_BRIDGE) -> Path:
    """The directory of a declared bundle, refusing a missing one.

    Refusal (not an empty path) is deliberate: a caller that is about to install or verify a
    bundle must not proceed against a directory that does not exist.
    """

    declared = _declared_bundle(name)
    path = package_root() / BUNDLE_DIRECTORY / name
    if not path.is_dir():
        raise RuntimeUnavailable(
            ENGINE, "a declared plugin bundle is missing from this package",
            detail=f"{path} (declared as {declared.get('path')})",
        )
    return path


def _declared_bundle(name: str) -> dict[str, Any]:
    closure = artifact_inventory().get("plugin_closure") or {}
    for entry in closure.get("bundles") or []:
        if isinstance(entry, dict) and _bundle_name_matches(entry, name):
            return entry
    raise RuntimeUnavailable(
        ENGINE, "a bundle was requested that the inventory does not declare", detail=name,
    )


def _bundle_name_matches(entry: Mapping[str, Any], name: str) -> bool:
    declared = str(entry.get("name") or "")
    # ``@scope/pkg`` and ``path/to/pkg`` in the same entry: either spelling identifies it.
    return declared.split("/")[-1] == name or Path(str(entry.get("path") or "")).name == name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bundle_status(name: str = STREAM_BRIDGE) -> dict[str, Any]:
    """Whether the declared bundle is present and byte-identical to its declaration.

    Reports facts; decides nothing. A caller that needs a usable bundle should call
    :func:`bundle_root` (which refuses when the directory is gone) or check ``problems``.
    """

    entry = _declared_bundle(name)
    path = package_root() / BUNDLE_DIRECTORY / name
    problems: list[str] = []
    files: list[dict[str, Any]] = []
    if not path.is_dir():
        problems.append(f"{path} is missing")
    for declared in entry.get("files") or []:
        if not isinstance(declared, dict):
            continue
        relative = str(declared.get("file") or "")
        target = path / relative
        record: dict[str, Any] = {"file": relative, "present": target.is_file()}
        if not record["present"]:
            problems.append(f"{relative} is missing")
        else:
            actual = _sha256(target)
            record["digest_matches"] = actual == str(declared.get("sha256") or "")
            if not record["digest_matches"]:
                problems.append(f"{relative} does not match its declared digest")
        files.append(record)
    return {
        "name": str(entry.get("name") or name),
        "version": str(entry.get("version") or ""),
        "path": str(path),
        "present": path.is_dir(),
        "files": files,
        "problems": problems,
    }


def describe() -> dict[str, Any]:
    """Diagnostic summary of the production inputs, for handoff records."""

    inventory = artifact_inventory()
    runtime = inventory.get("runtime") or {}
    return {
        "inventory": str(artifacts_path()),
        "declared_version": runtime.get("declared_version"),
        "source_tag": runtime.get("source_tag"),
        "bundles": [bundle_status(name)["name"] for name in _declared_bundle_names()],
    }


def _declared_bundle_names() -> list[str]:
    closure = artifact_inventory().get("plugin_closure") or {}
    names = []
    for entry in closure.get("bundles") or []:
        if isinstance(entry, dict):
            directory = Path(str(entry.get("path") or "")).name
            if directory:
                names.append(directory)
    return names
