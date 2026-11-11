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
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

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


# --------------------------------------------------------------------------------------
# Installed application: what the installer stages, and how a launch finds it
# --------------------------------------------------------------------------------------
#
# Two facts a launch needs are *not* in the wheel Odessy ships: the runtime executable
# (a 273 MB platform artifact the installer downloads once per machine) and the composed
# profile (the runtime's own profile directory, which `dsh plugin add` would otherwise
# build with pnpm). Both are described here so that the installer and the launch read one
# declaration instead of two, and so that "installed" can be verified rather than assumed.

#: Environment variable the installed launcher exports: the directory the installer staged
#: the runtime into. Nothing searches for it; an unset variable means "not installed".
INSTALLED_ENV = "ODESSY_HARNESS_RUNTIME_DIR"

#: The bundle package this repository owns, as it appears in a composed profile.
OWN_BUNDLE_PACKAGE = "@odessy/dsh-stream-bridge"

#: Suffix the profile's own package name carries, mirroring the pin's own naming.
_PROFILE_PACKAGE_NAME = "dsh-profile-"

#: The patch layer a person may edit. Seeded empty once and never rewritten.
PROFILE_EDITABLE_PATCH = "cordis.patch.yml"

_PROFILE_ROOT_TEXT = """\
# dsh profile root - an empty entry list. The tree is composed as patches:
# each bundle in package.json's dsh.profile.bundles, then cordis.patch.yml, then any
# --patch overlays. Edit cordis.patch.yml, not this file.
[]
"""

_PROFILE_PATCH_TEXT = """\
# The operator's patch layer for this dsh profile, applied after every bundle layer.
# Odessy seeds this file empty and never rewrites it; the narrow tool overlay is passed
# as --patch by odessy.runtime_backends.harness at launch instead of being written here.
[]
"""


def runtime_wheel(tag: str = "win_amd64") -> dict[str, Any]:
    """The declared runtime wheel, as an installer needs it.

    Refuses rather than returning a partial record: an inventory entry without a digest or
    a URL is not something a build should download.
    """

    wheels = (artifact_inventory().get("runtime") or {}).get("wheels") or {}
    declared = wheels.get(tag)
    if not isinstance(declared, dict):
        raise RuntimeUnavailable(
            ENGINE, "the artifact inventory declares no runtime wheel for this platform",
            detail=f"harness-artifacts.json: runtime.wheels['{tag}']",
        )
    for name in ("file", "sha256", "bytes", "url", "archive_prefix"):
        if not declared.get(name):
            raise RuntimeUnavailable(
                ENGINE, "a declared runtime wheel is incomplete",
                detail=f"harness-artifacts.json: runtime.wheels['{tag}'].{name}",
            )
    return dict(declared)


def executable_declaration(target: str = "win-x64") -> dict[str, Any]:
    """The declared executable and its sidecars for a platform target."""

    executables = (artifact_inventory().get("runtime") or {}).get("executables") or {}
    declared = executables.get(target)
    if not isinstance(declared, dict) or not declared.get("file"):
        raise RuntimeUnavailable(
            ENGINE, "the artifact inventory declares no runtime executable for this target",
            detail=f"harness-artifacts.json: runtime.executables['{target}']",
        )
    return dict(declared)


def _file_problem(path: Path, declared: Mapping[str, Any]) -> str | None:
    """``None`` when ``path`` is exactly the declared file, else what is wrong with it."""

    if not path.is_file():
        return f"{path} is missing"
    expected_size = declared.get("bytes")
    if isinstance(expected_size, int) and path.stat().st_size != expected_size:
        return (f"{path} is {path.stat().st_size} bytes, declared {expected_size}")
    actual = _sha256(path)
    expected = str(declared.get("sha256") or "")
    if actual != expected:
        return f"{path} sha256 {actual[:16]}... does not match the declared {expected[:16]}..."
    return None


def verify_runtime_assets(runtime_dir: str | os.PathLike[str], *,
                          target: str = "win-x64") -> dict[str, Any]:
    """Check a staged runtime directory against the declaration, or refuse.

    Every file is size- and digest-checked before it is named, so "the runtime is installed"
    is a measurement. A missing or edited file refuses with the file and the expected digest
    in the message: a replacement is a deliberate upgrade, not an automatic repair.
    """

    root = Path(runtime_dir)
    declared = executable_declaration(target)
    executable = root / str(declared["file"])
    problems = []
    problem = _file_problem(executable, declared)
    if problem:
        problems.append(problem)
    for sidecar in declared.get("sidecars") or []:
        if not isinstance(sidecar, dict) or not sidecar.get("file"):
            problems.append("the declaration contains a sidecar without a file name")
            continue
        side = root / str(sidecar["file"])
        detail = _file_problem(side, sidecar)
        if detail:
            problems.append(detail)
    if problems:
        raise RuntimeUnavailable(
            ENGINE, "the installed Harness runtime does not match its declaration",
            detail="; ".join(problems),
        )
    return {
        "runtime_dir": str(root),
        "executable": str(executable),
        "version": str(declared.get("reported_version") or ""),
        "sha256": str(declared.get("sha256") or ""),
        "bytes": int(declared.get("bytes") or 0),
        "sidecars": [str(item.get("file")) for item in declared.get("sidecars") or []
                     if isinstance(item, dict)],
    }


def installed_runtime_dir(environ: Mapping[str, str] | None = None) -> Path | None:
    """The directory the installer staged the runtime into, if this is an installed app.

    ``None`` means "not told", not "not installed": the caller decides whether that is an
    error (a harness launch) or irrelevant (legacy sessions). No discovery, no ``PATH``
    scan, no sibling-checkout guess.
    """

    environ = os.environ if environ is None else environ
    raw = (environ.get(INSTALLED_ENV) or "").strip()
    return Path(raw).expanduser() if raw else None


def installed_executable(environ: Mapping[str, str] | None = None) -> Path:
    """The executable a *production* launch must use, verifying the staged payload.

    This is the resolution that replaces ``ODESSY_HARNESS_EXE`` for an installed
    application. It refuses when the variable is unset, when the directory was moved, and
    when any staged byte differs from the declaration.
    """

    runtime_dir = installed_runtime_dir(environ)
    if runtime_dir is None:
        raise RuntimeUnavailable(
            ENGINE, "not configured: no Harness runtime is installed or named",
            detail=(f"set {INSTALLED_ENV} to the staged runtime directory (the installed "
                    "launcher does this), or ODESSY_HARNESS_EXE for development"),
        )
    assets = verify_runtime_assets(runtime_dir)
    return Path(assets["executable"])


def installed_node(environ: Mapping[str, str] | None = None) -> Path | None:
    """The private Node that runs the separately launched tool server, if one is staged.

    The runtime executable is a single-executable application: it embeds Node but exposes
    no interpreter, so the tool server cannot borrow it. Two layouts are recognized, because
    both are produced by the installers: the legacy component format stages
    ``runtime/node-runtime/node.exe`` beside ``runtime/harness-runtime``; the versioned
    payload stages ``node/node.exe`` beside ``harness/`` inside the active version.
    """

    runtime_dir = installed_runtime_dir(environ)
    if runtime_dir is None:
        return None
    for candidate in (runtime_dir.parent / "node-runtime" / "node.exe",
                      runtime_dir.parent / "node" / "node.exe",
                      runtime_dir.parent / "node" / "bin" / "node"):
        if candidate.is_file():
            return candidate
    return None


def installed_node_tools_dir(environ: Mapping[str, str] | None = None) -> Path | None:
    """The installed copy of the reviewed Node tool server, if one is staged.

    The legacy assembler lays the payload out as ``<root>/runtime/harness-runtime`` and
    ``<root>/application/tools`` — not ``<root>/tools``. Resolving from the runtime directory
    therefore takes two parents, then ``application/tools``, which is exactly the path
    ``templates/launch.py`` exports and the tool server's entry lives under. The versioned
    payload places ``tools/`` next to ``harness/`` inside the active version instead.
    """

    runtime_dir = installed_runtime_dir(environ)
    if runtime_dir is None:
        return None
    for candidate in (runtime_dir.parent.parent / "application" / "tools",
                      runtime_dir.parent / "tools"):
        if candidate.is_dir():
            return candidate
    return None


# --------------------------------------------------------------------------------------
# The composed profile
# --------------------------------------------------------------------------------------

def declared_profile() -> dict[str, Any]:
    """The composed profile declaration from the inventory."""

    profile = (artifact_inventory().get("plugin_closure") or {}).get("profile")
    if not isinstance(profile, dict) or not profile.get("name"):
        raise RuntimeUnavailable(
            ENGINE, "the artifact inventory declares no composed profile",
            detail="harness-artifacts.json: plugin_closure.profile",
        )
    bundles = profile.get("bundles")
    if not isinstance(bundles, list) or OWN_BUNDLE_PACKAGE not in bundles:
        raise RuntimeUnavailable(
            ENGINE, "the composed profile does not declare Odessy's own bundle",
            detail=f"harness-artifacts.json: plugin_closure.profile.bundles ({OWN_BUNDLE_PACKAGE})",
        )
    return dict(profile)


def profile_package_text(bundles: Sequence[str], *, name: str = "") -> str:
    """The profile's ``package.json`` text, generated rather than shipped.

    Deterministic on purpose: the same inventory produces the same bytes, so a repair can
    be a comparison and a test can assert the exact text.
    """

    document = {
        "name": name or (_PROFILE_PACKAGE_NAME + "acp"),
        "private": True,
        "dependencies": {},
        "dsh": {"profile": {"bundles": list(bundles)}},
    }
    return json.dumps(document, indent=2) + "\n"


def compose_profile(profile_dir: str | os.PathLike[str], *,
                    profile: str = "") -> dict[str, Any]:
    """Write the declared profile into ``profile_dir`` and report exactly what happened.

    Why not ``dsh plugin add``: that shells out to pnpm, and an installed Odessy must not
    require a package manager or a network fetch to start its own engine. The composed
    result is three small text files plus a byte-identical copy of the packaged bundle, so
    it can be written directly and verified against the same digests the package declares.

    Rules, in order of importance:

    - The packaged bundle is verified **before** anything is written; a corrupt bundle
      refuses instead of installing a profile that cannot load.
    - A file that is already correct is not rewritten (idempotent, mtime-stable).
    - The operator's own ``cordis.patch.yml`` is created once and never overwritten.
    - A ``package.json`` that already names additional bundles keeps them; Odessy's bundle
      is *added* to the list and the addition is reported, never silent.
    """

    declared = declared_profile()
    bundles = [str(item) for item in declared["bundles"]]
    status = bundle_status()
    if status["problems"]:
        raise RuntimeUnavailable(
            ENGINE, "the packaged plugin bundle cannot be installed",
            detail="; ".join(status["problems"]),
        )
    directory = Path(profile_dir)
    directory.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "profile": profile or str(declared["name"]),
        "directory": str(directory),
        "wrote": [],
        "kept": [],
        "added_bundle": False,
        "bundle": "current",
    }

    package_path = directory / "package.json"
    if package_path.is_file():
        try:
            existing = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeUnavailable(
                ENGINE, "the Harness profile's package.json is not readable JSON",
                detail=f"{package_path}: {exc}",
            ) from exc
        if not isinstance(existing, dict):
            raise RuntimeUnavailable(
                ENGINE, "the Harness profile's package.json is not an object",
                detail=str(package_path),
            )
        current = ((existing.get("dsh") or {}).get("profile") or {}).get("bundles")
        current = [str(item) for item in current] if isinstance(current, list) else []
        if OWN_BUNDLE_PACKAGE in current:
            record["kept"].append("package.json")
        else:
            existing.setdefault("dsh", {}).setdefault("profile", {})
            existing["dsh"]["profile"]["bundles"] = [*current, OWN_BUNDLE_PACKAGE]
            _write_text(package_path, json.dumps(existing, indent=2) + "\n")
            record["wrote"].append("package.json")
            record["added_bundle"] = True
    else:
        _write_text(package_path, profile_package_text(
            bundles, name=_PROFILE_PACKAGE_NAME + str(record["profile"])))
        record["wrote"].append("package.json")

    for name, text in (("cordis.yml", _PROFILE_ROOT_TEXT),
                       (PROFILE_EDITABLE_PATCH, _PROFILE_PATCH_TEXT)):
        target = directory / name
        if target.is_file():
            record["kept"].append(name)
            continue
        _write_text(target, text)
        record["wrote"].append(name)

    bundle_target = directory / str(declared["bundle_directory"])
    source = bundle_root()
    existed = bundle_target.is_dir()
    if _bundle_matches(bundle_target):
        record["kept"].append("bundle")
    else:
        _replace_bundle(source, bundle_target)
        record["wrote"].append("bundle")
        record["bundle"] = "repaired" if existed else "installed"
    return record


def _write_text(path: Path, text: str) -> None:
    """Write ``text`` unless it is already there; never leave a partial file."""

    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".odessy-tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _bundle_matches(directory: Path) -> bool:
    """Whether ``directory`` already holds the declared bundle, byte for byte."""

    entry = _declared_bundle(STREAM_BRIDGE)
    files = [item for item in entry.get("files") or [] if isinstance(item, dict)]
    if not files or not directory.is_dir():
        return False
    for declared in files:
        relative = str(declared.get("file") or "")
        target = directory / relative
        if not relative or not target.is_file():
            return False
        if _sha256(target) != str(declared.get("sha256") or ""):
            return False
    return True


def _replace_bundle(source: Path, target: Path) -> None:
    """Install the packaged bundle at ``target``, replacing a stale copy file by file.

    Only files the inventory declares are written, and each is written to a temporary name
    first, so an interrupted copy leaves the old copy rather than half of a new one.
    """

    entry = _declared_bundle(Path(source).name)
    for declared in entry.get("files") or []:
        if not isinstance(declared, dict):
            continue
        relative = str(declared.get("file") or "")
        origin = source / relative
        if not origin.is_file():
            raise RuntimeUnavailable(
                ENGINE, "a packaged plugin bundle file is missing", detail=str(origin),
            )
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".odessy-tmp")
        temporary.write_bytes(origin.read_bytes())
        os.replace(temporary, destination)
