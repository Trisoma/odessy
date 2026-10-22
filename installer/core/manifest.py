"""Local release manifest validation; authenticity is established by distribution."""
from __future__ import annotations
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

class ManifestError(ValueError):
    pass

def validate_https_url(value: object) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 33 for c in value):
        raise ManifestError("Expected a nonempty HTTPS URL without whitespace.")
    try:
        parts = urlsplit(value)
        parts.port
    except ValueError as error:
        raise ManifestError("Invalid artifact URL.") from error
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.fragment or chr(92) in value):
        raise ManifestError("URLs require HTTPS, a host, no credentials and no fragment.")
    return value

@dataclass(frozen=True)
class Artifact:
    id: str
    version: str
    url: str
    size_bytes: int
    sha256: str

    def __post_init__(self):
        if not isinstance(self.id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.id):
            raise ManifestError("Invalid artifact id.")
        if (not isinstance(self.version, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", self.version)
                or self.version.lower() in {"latest", "main", "master", "develop", "head"}):
            raise ManifestError("An explicit release version is required.")
        validate_https_url(self.url)
        if type(self.size_bytes) is not int or self.size_bytes <= 0:
            raise ManifestError("size_bytes must be a positive integer.")
        if not isinstance(self.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ManifestError("sha256 must be 64 lowercase hexadecimal characters.")

@dataclass(frozen=True)
class Manifest:
    release: str
    artifacts: tuple[Artifact, ...]

    @property
    def download_bytes(self) -> int:
        return sum(a.size_bytes for a in self.artifacts)

def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result

def load_manifest(path: Path) -> Manifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_keys)
    except (OSError, ValueError) as error:
        raise ManifestError(f"Cannot read a valid manifest: {error}") from error
    if not isinstance(data, dict) or set(data) != {"schema_version", "release", "platform", "artifacts"}:
        raise ManifestError("Expected schema_version, release, platform and artifacts only.")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ManifestError("Only schema version 1 is supported.")
    if data["platform"] != "windows-x64":
        raise ManifestError("Manifest must target windows-x64.")
    if not isinstance(data["release"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", data["release"]):
        raise ManifestError("A release identifier is required.")
    rows = data["artifacts"]
    if not isinstance(rows, list) or not rows or len(rows) > 100:
        raise ManifestError("Expected 1 to 100 artifacts.")
    artifacts, ids = [], set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "version", "url", "size_bytes", "sha256"}:
            raise ManifestError("Each artifact requires id, version, url, size_bytes and sha256 only.")
        artifact = Artifact(**row)
        if artifact.id in ids:
            raise ManifestError(f"Duplicate artifact id: {artifact.id}")
        ids.add(artifact.id)
        artifacts.append(artifact)
    return Manifest(data["release"], tuple(artifacts))
