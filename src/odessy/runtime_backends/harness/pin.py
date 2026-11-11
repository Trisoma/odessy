"""The one Harness runtime this build is allowed to launch.

The pin is data, not discovery. Nothing here searches ``PATH``, a sibling checkout or a
site-packages neighbour: :func:`odessy.runtime_backends.harness.config.from_environment`
accepts exactly one executable path, and :func:`verify_version` refuses to continue when
the executable at that path is not the pinned build.

The values below are read from :mod:`odessy.runtime_backends.harness.resources`'
``harness-artifacts.json``, which ships inside this package. That file is the single
declaration: the digest a launch enforces and the digest an installer reads are the same
fact, and a build that shipped the two apart could not disagree silently. Updating the pin
is a reviewed change to that file plus a re-run of the acceptance gate in
``experiments/restructure``.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from odessy.runtime_backends.contracts import RuntimeUnavailable
from odessy.runtime_backends.harness.resources import artifact_inventory


def _declared(mapping: Mapping[str, Any], name: str, where: str) -> Any:
    """A required value from the inventory, refused by name rather than by ``KeyError``."""

    if name not in mapping or mapping[name] in (None, ""):
        raise RuntimeUnavailable(
            "harness", "the artifact inventory does not declare a required value",
            detail=f"harness-artifacts.json: {where}.{name}",
        )
    return mapping[name]


_RUNTIME = artifact_inventory()["runtime"]
_EXECUTABLES = _RUNTIME.get("executables") or {}
_WINDOWS = _EXECUTABLES.get("win-x64") or {}
if not _WINDOWS:
    raise RuntimeUnavailable(
        "harness", "the artifact inventory declares no win-x64 executable",
        detail="harness-artifacts.json: runtime.executables['win-x64']",
    )

#: Harness release the migration targets (``deepseek-harness-*`` 0.2.1rc2).
PINNED_VERSION = str(_declared(_RUNTIME, "declared_version", "runtime"))
PINNED_SOURCE_TAG = str(_declared(_RUNTIME, "source_tag", "runtime"))
PINNED_SOURCE_COMMIT = str(_declared(_RUNTIME, "source_commit", "runtime"))

#: Distribution artifacts verified while selecting the pin (wheel platform tag -> sha256).
PINNED_WHEEL_SHA256: dict[str, str] = {
    str(tag): str(declared.get("sha256") or "")
    for tag, declared in (_RUNTIME.get("wheels") or {}).items()
    if isinstance(declared, dict)
}

#: The unpacked executable shipped inside the ``win_amd64`` wheel.
PINNED_EXECUTABLE_SHA256 = str(_declared(_WINDOWS, "sha256", "runtime.executables['win-x64']"))
PINNED_EXECUTABLE_SIZE = int(_declared(_WINDOWS, "bytes", "runtime.executables['win-x64']"))
#: Where that executable sits inside an installed runtime-bin wheel.
PINNED_EXECUTABLE_PATH = str(_declared(
    _WINDOWS, "path_in_wheel", "runtime.executables['win-x64']"))

#: Launch contract: the ACP profile speaks JSON-RPC over stdio (contract §3).
PINNED_PROFILE = str(_RUNTIME.get("profile") or "acp")
ACP_PROTOCOL_VERSION = 1

_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?")


def parse_version(output: str) -> str:
    """Extract the version token from ``--version`` output (last match wins)."""
    matches = _VERSION_PATTERN.findall(output or "")
    return matches[-1] if matches else ""


def _digest(path: Path) -> tuple[str, int]:
    """Compute the executable digest and size without loading the artifact into memory."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def verify_version(
    command: Sequence[str],
    expected: str = PINNED_VERSION,
    *,
    env: Mapping[str, str],
    timeout: float = 30.0,
    engine: str = "harness",
    expected_sha256: str | None = PINNED_EXECUTABLE_SHA256,
) -> dict[str, object]:
    """Run ``<command> --version`` and refuse anything but the pinned build.

    ``env`` is the *launch* environment, not the parent's: the probe must not be able to
    succeed only because the caller happened to have credentials or a proxy exported.

    ``expected_sha256`` is checked whenever the first argv element is a file, so a build
    that reports the right version but is not the reviewed artifact is refused rather
    than accepted as "unverified". Returns the identity recorded for diagnostics.
    """
    argv = [*command, "--version"]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dict(env),
            # No shell, no inherited cwd dependency: the executable is the whole input.
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeUnavailable(engine, "the configured executable does not exist",
                                 detail=str(command[0])) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeUnavailable(
            engine, "the configured executable did not report a version",
            detail=f"no answer within {timeout:g}s",
        ) from exc
    except OSError as exc:
        raise RuntimeUnavailable(engine, "the configured executable cannot be launched",
                                 detail=str(exc)) from exc

    output = f"{completed.stdout}\n{completed.stderr}".strip()
    reported = parse_version(output)
    if completed.returncode != 0 or not reported:
        raise RuntimeUnavailable(
            engine, "the configured executable is not a Harness runtime",
            detail=(output.splitlines() or ["no output"])[-1][:200],
        )
    if reported != expected:
        raise RuntimeUnavailable(
            engine, "the configured executable is not the pinned build",
            detail=f"reported {reported}, pinned {expected}",
        )

    first = Path(command[0])
    digest, size = "", 0
    if first.is_file():
        try:
            digest, size = _digest(first)
        except OSError as exc:
            raise RuntimeUnavailable(
                engine, "the configured executable cannot be read for identity verification",
                detail=f"{first}: {exc}",
            ) from exc

    if expected_sha256 is not None:
        if not digest:
            raise RuntimeUnavailable(
                engine, "the configured executable is not a verifiable artifact",
                detail=str(first),
            )
        if digest != expected_sha256:
            raise RuntimeUnavailable(
                engine, "the configured executable is not the pinned artifact",
                detail=f"sha256 {digest[:16]}… does not match the pinned {expected_sha256[:16]}…",
            )

    return {
        "version": reported,
        "pinned_version": expected,
        "source_tag": PINNED_SOURCE_TAG,
        "command": list(command),
        "sha256": digest,
        "size": size,
        "artifact": "pinned" if digest == PINNED_EXECUTABLE_SHA256 else "unverified",
    }
