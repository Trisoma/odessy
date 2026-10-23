from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


class WorkspaceAuthorityError(PermissionError):
    """Raised when Odessy cannot safely authorize a workspace path."""


def _native_path_text(candidate_path: str) -> str:
    """The path text to judge, read so that a Windows path cannot pass on POSIX.

    The policy can run on a different OS from the executor: in the two-machine setup it
    runs on the Linux Jetson and the laptop executing the call runs Windows. On POSIX,
    ``C:\\Windows\\System32\\x`` is one relative file name, so it resolved inside the
    workspace and a write there was only confirmed. Here a Windows-anchored path (a
    drive or a UNC share) is outside, and a backslash is a separator, so ``..\\..``
    climbs as it would on Windows. On Windows the native rules already apply.
    """

    windows = PureWindowsPath(candidate_path)
    # C:relative is tied to per-drive process state, even when C: happens to be
    # the workspace drive. Never silently reinterpret it as a workspace path.
    if windows.drive and not windows.root:
        raise WorkspaceAuthorityError("drive-relative paths are not authorized")
    if os.name == "nt":
        return candidate_path
    if windows.drive:
        raise WorkspaceAuthorityError("path is outside the allowed workspace")
    return candidate_path.replace("\\", "/")


@dataclass(frozen=True)
class WorkspacePath:
    requested: str
    resolved: Path
    relative: str
    exists: bool
    sensitive: bool


@dataclass(frozen=True)
class WorkspaceAuthority:
    """Canonical local workspace boundary for Odessy v0.1.0.

    A path is inside the workspace only when its canonical target, or the
    canonical nearest existing ancestor for non-existent paths, remains inside
    the canonical workspace root. This is a user-space check, so TOCTOU races
    remain possible if the filesystem changes between validation and use.
    """

    workspace_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_root", self.workspace_root.resolve())

    def resolve_path(self, candidate_path: str, *, must_exist: bool = False) -> WorkspacePath:
        if not isinstance(candidate_path, str) or not candidate_path.strip():
            raise WorkspaceAuthorityError("path is empty or invalid")

        candidate = Path(_native_path_text(candidate_path)).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate

        try:
            resolved = candidate.resolve(strict=must_exist)
            # Reject outside paths before stat/exists can contact a UNC share.
            self._assert_inside(resolved)
            exists = resolved.exists()
            if not exists:
                resolved = self._resolve_nonexistent(candidate)
                self._assert_inside(resolved)
                exists = resolved.exists()
        except OSError as exc:
            raise WorkspaceAuthorityError("path cannot be resolved safely") from exc

        self._assert_inside(resolved)
        relative = self._relative(resolved)
        sensitive = is_sensitive_workspace_path(relative)
        return WorkspacePath(
            requested=candidate_path,
            resolved=resolved,
            relative=relative,
            exists=exists,
            sensitive=sensitive,
        )

    def resolve_cwd(self, cwd: str) -> Path:
        resolved = self.resolve_path(cwd, must_exist=True)
        if not resolved.resolved.is_dir():
            raise WorkspaceAuthorityError("cwd is not an authorized directory")
        return resolved.resolved

    def _resolve_nonexistent(self, candidate: Path) -> Path:
        parts_to_reapply: list[str] = []
        current = candidate
        while not current.exists():
            if current == current.parent:
                raise WorkspaceAuthorityError("path has no resolvable ancestor")
            parts_to_reapply.append(current.name)
            current = current.parent

        ancestor = current.resolve(strict=True)
        self._assert_inside(ancestor)
        # The reapplied parts do not exist, so none is a symlink and ".." can be folded
        # lexically. Appending it verbatim let `missing/../../outside` pass on POSIX:
        # the lexical relative_to check saw it under the root. Windows resolves ".."
        # before this point, which is why only Linux CI caught it.
        resolved = ancestor
        for part in reversed(parts_to_reapply):
            if part == "..":
                resolved = resolved.parent
            elif part not in ("", "."):
                resolved = resolved / part
        return resolved

    def _assert_inside(self, resolved: Path) -> None:
        root = self.workspace_root
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise WorkspaceAuthorityError("path is outside the allowed workspace") from exc

    def _relative(self, resolved: Path) -> str:
        try:
            relative = resolved.relative_to(self.workspace_root)
        except ValueError as exc:
            raise WorkspaceAuthorityError("path is outside the allowed workspace") from exc
        text = relative.as_posix()
        return text if text else "."


_SENSITIVE_SEGMENTS = {
    ".aws",
    ".azure",
    ".docker",
    ".git-credentials",
    ".gnupg",
    ".kube",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".ssh",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}

_SENSITIVE_BASENAME_PATTERNS = {
    ".env",
    ".env.*",
    "*.key",
    "*.pem",
    "*credential*",
    "*credentials*",
    "*secret*",
    "*secrets*",
    "*token*",
}


def is_sensitive_workspace_path(relative_path: str) -> bool:
    parts = [part.lower() for part in relative_path.replace("\\", "/").split("/") if part]
    if any(part in _SENSITIVE_SEGMENTS for part in parts):
        return True
    basename = parts[-1] if parts else ""
    return any(fnmatch.fnmatchcase(basename, pattern) for pattern in _SENSITIVE_BASENAME_PATTERNS)
