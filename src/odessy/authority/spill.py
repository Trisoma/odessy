"""Local spill storage: keep withheld output recoverable.

Ported from DeepSeek Harness `packages/spill/spill-local`, reduced to what
Odessy needs. Upstream splits the concern in two -- a backend that stores bytes
and a policy plugin that decides when to spill -- because a host may mount a
different backend. Odessy has one host and one backend, so the two collapse into
this module; the *seam* is preserved by keeping retention (`output_retention.py`)
free of I/O.

The property that matters: **spilling never destroys.** If a write fails,
`save_text` returns `None` and the caller keeps the original content. There is no
path in this module that converts a storage failure into data loss.

Location is inside the workspace root, under `.odessy/spill/<session>/`. That is
deliberate: the existing `WorkspaceAuthority` already bounds every path to the
workspace, so putting spills there means retrieval inherits that boundary for
free instead of needing a second, weaker one. It also means a bug in retrieval
cannot reach outside the workspace even if a locator is forged.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from odessy.authority.workspace_authority import WorkspaceAuthority

#: Spill root, relative to the workspace root. Dot-prefixed so tooling treats it
#: as metadata rather than content.
SPILL_DIRNAME = ".odessy/spill"

#: Filename components are reduced to this alphabet, so no caller-supplied label
#: can introduce a separator, a `..` segment, or a drive-relative path.
_SAFE_COMPONENT = re.compile(r"[^a-z0-9._-]+")

#: Digest prefix length. 16 hex characters is far past collision risk for the
#: number of spills one machine will ever hold, and stays readable.
_DIGEST_CHARS = 16


def _safe_component(value: str, *, fallback: str) -> str:
    cleaned = _SAFE_COMPONENT.sub("-", value.strip().lower()).strip("-.")
    return cleaned[:64] or fallback


def _write_atomically(target: Path, payload: bytes) -> None:
    """Write ``payload`` to ``target`` so a partial file is never visible.

    A unique temp name is used rather than a predictable one such as
    ``<target>.<pid>.tmp``. A per-process name collides when the same content is
    spilled twice in one process -- the common case, since identical output
    resolves to the same content-addressed target -- and on Windows that
    collision surfaced as a transient `OSError` that made a *successful* second
    write report `None`, which the caller would read as a storage failure.

    One retry covers Windows sharing violations, which are transient by nature.
    """

    descriptor, temp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(2):
            try:
                os.replace(temp_name, target)
                return
            except OSError:
                if attempt == 1:
                    raise
    except OSError:
        # Never leave the temp file behind on a failed write.
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


@dataclass(frozen=True)
class SpillLocator:
    """Enough metadata to identify and recover a spilled payload.

    Carries both an absolute and a workspace-relative path: the relative form is
    what goes to the model (no host paths in the conversation), the absolute form
    is what retrieval validates.
    """

    path: str
    relative_path: str
    byte_count: int
    line_count: int
    digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "relative_path": self.relative_path,
            "byte_count": self.byte_count,
            "line_count": self.line_count,
            "digest": self.digest,
        }


def _default_session_id() -> str:
    """Per-process session identity.

    Odessy runs one bounded turn per process, so the process is the session.
    Cheap, and it makes cleanup a matter of deleting a directory.
    """

    return f"{os.getpid()}-{int(time.time())}"


@dataclass(frozen=True)
class SpillStore:
    """Content-addressed local spill storage inside the workspace boundary."""

    workspace_root: Path
    session_id: str = field(default_factory=_default_session_id)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_root", Path(self.workspace_root).resolve())
        object.__setattr__(
            self, "session_id", _safe_component(self.session_id, fallback="session")
        )

    @property
    def directory(self) -> Path:
        return self.workspace_root / SPILL_DIRNAME / self.session_id

    def save_text(self, text: str, *, label: str = "output") -> SpillLocator | None:
        """Persist ``text`` and return its locator, or `None` if storage failed.

        `None` is the honest failure signal: the caller must then keep the
        original content rather than substituting a lossy truncation.
        """

        try:
            payload = text.encode("utf-8")
        except UnicodeEncodeError:
            # Surrogates from a mangled decode. Recoverable, so do not lose data.
            payload = text.encode("utf-8", "replace")

        digest = hashlib.sha256(payload).hexdigest()
        safe_label = _safe_component(label, fallback="output")
        filename = f"{safe_label}-{digest[:_DIGEST_CHARS]}-{len(payload)}.txt"
        target = self.directory / filename

        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            _write_atomically(target, payload)
        except OSError:
            return None

        try:
            relative = target.relative_to(self.workspace_root).as_posix()
        except ValueError:
            return None

        return SpillLocator(
            path=str(target),
            relative_path=relative,
            byte_count=len(payload),
            line_count=len(text.splitlines()),
            digest=digest[:_DIGEST_CHARS],
        )

    def read_text(self, locator: SpillLocator) -> str:
        """Recover spilled content, bounded by the workspace authority.

        The locator is treated as untrusted input: a forged or corrupted path is
        rejected by `WorkspaceAuthority` rather than followed.
        """

        authority = WorkspaceAuthority(self.workspace_root)
        resolved = authority.resolve_path(locator.path, must_exist=True)
        if resolved.sensitive:
            raise PermissionError("spill path resolves to a sensitive file")
        if not resolved.resolved.is_file():
            raise FileNotFoundError(locator.path)
        return resolved.resolved.read_text(encoding="utf-8", errors="replace")


def spill_store_for(workspace_root: Path | str) -> SpillStore:
    """Build a store bound to a workspace root."""

    return SpillStore(workspace_root=Path(workspace_root))
