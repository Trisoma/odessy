"""Explicit, verifiable installation of wake-word model assets.

The pretrained openWakeWord models are **not** Apache-2.0. The code is Apache-2.0;
the bundled pretrained weights are CC BY-NC-SA 4.0, which forbids commercial use.
They therefore never live in this repository and are never fetched implicitly:
``odessy voice --setup-wake`` is the only path, it writes outside the checkout, and it
records where each file came from, which version it is, and its checksum.

A manifest entry without a pinned ``sha256`` is *not* verified, and the installer says
so instead of implying otherwise. The default model is pinned: its digest was computed
from the release asset itself, and a mismatch aborts the install before anything is
written. A digest that cannot be pinned is still handled — it is recorded as
unverified with the observed value printed, rather than implied to be verified.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

FETCH_TIMEOUT_SECONDS = 120

#: Where assets go. Deliberately outside the repository and outside the config dir:
#: these are downloaded blobs, not settings.
DEFAULT_WAKE_ASSET_DIR = Path.home() / ".local" / "share" / "odessy" / "wake"

#: The default phrase. "Hey Jarvis" is a pretrained model; a custom "Hey Odessy"
#: phrase needs its own training run and is out of scope here.
DEFAULT_WAKE_MODEL = "hey_jarvis_v0.1.onnx"


@dataclass(frozen=True)
class WakeAsset:
    name: str
    url: str
    license: str
    version: str
    #: Hex SHA-256, or ``None`` when the digest has not been pinned yet.
    sha256: str | None = None


#: One entry per file the detector loads. The digest was computed from the release
#: asset itself (1,271,370 bytes) and is what ``--setup-wake`` verifies against; a
#: changed digest means the file is not the reviewed one.
WAKE_ASSETS: tuple[WakeAsset, ...] = (
    WakeAsset(
        name=DEFAULT_WAKE_MODEL,
        url="https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/hey_jarvis_v0.1.onnx",
        license="CC-BY-NC-SA-4.0",
        version="openWakeWord v0.5.1",
        sha256="94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb",
    ),
)


class WakeAssetError(RuntimeError):
    """An asset could not be fetched, verified, or written."""


def asset_dir(environ: dict[str, str] | None = None) -> Path:
    source = os.environ if environ is None else environ
    override = source.get("ODESSY_WAKE_MODEL", "").strip()
    if override:
        return Path(override).expanduser().parent
    return DEFAULT_WAKE_ASSET_DIR


def manifest_path(directory: Path) -> Path:
    return directory / "assets.json"


def load_manifest(directory: Path) -> dict[str, bool]:
    """Recorded assets and whether each one was verified against a pinned digest."""

    try:
        data = json.loads(manifest_path(directory).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    recorded: dict[str, bool] = {}
    if not isinstance(data, dict):
        return recorded
    for record in data.get("assets", []):
        if isinstance(record, dict) and isinstance(record.get("name"), str):
            recorded[record["name"]] = bool(record.get("verified"))
    return recorded


def install(
    directory: Path,
    *,
    assets: tuple[WakeAsset, ...] = WAKE_ASSETS,
    fetcher: Callable[[str], bytes] | None = None,
    overwrite: bool = False,
) -> list[str]:
    """Fetch and record the assets. Returns human-readable lines describing what happened."""

    fetch = fetcher or _fetch
    directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, str | bool | None]] = []
    lines: list[str] = []
    for asset in assets:
        target = directory / asset.name
        if target.exists() and not overwrite:
            data = target.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            verified = asset.sha256 is not None and digest == asset.sha256
            lines.append(f"{asset.name}: already present ({'verified' if verified else 'unverified'})")
        else:
            try:
                data = fetch(asset.url)
            except Exception as error:  # noqa: BLE001 - reported, not raised upward
                raise WakeAssetError(f"{asset.name}: download failed: {error}") from error
            digest = hashlib.sha256(data).hexdigest()
            if asset.sha256 is not None and digest != asset.sha256:
                raise WakeAssetError(
                    f"{asset.name}: checksum mismatch (expected {asset.sha256}, got {digest}); "
                    "nothing was written"
                )
            target.write_bytes(data)
            verified = asset.sha256 is not None
            lines.append(
                f"{asset.name}: downloaded from {asset.url} "
                f"({'verified' if verified else 'unverified'}, sha256 {digest})"
            )
            if not verified:
                lines.append(
                    f"  pin this digest in WAKE_ASSETS to make the install verifiable: {digest}"
                )
        records.append(
            {
                **asdict(asset),
                "sha256_observed": digest,
                "verified": verified,
                "license": asset.license,
            }
        )
    manifest_path(directory).write_text(
        json.dumps({"assets": records, "code_license": "Apache-2.0"}, indent=2) + "\n",
        encoding="utf-8",
    )
    lines.append(f"manifest written to {manifest_path(directory)}")
    return lines


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "odessy-setup-wake"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        return response.read()
