"""Path identity helpers that stay correct under Windows 8.3 short names."""
from __future__ import annotations
import os
import stat
from pathlib import Path

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def spelled(path) -> str:
    """Absolute, case-folded spelling that never expands a short (8.3) name."""
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def same(left, right) -> bool:
    """True when both values name the same location.

    ``C:\\Users\\RUNNER~1`` and ``C:\\Users\\runneradmin`` are one directory on
    the same volume, so the exact spelling is compared first and the resolved
    form only as a fallback.
    """
    if spelled(left) == spelled(right):
        return True
    return os.path.normcase(os.path.realpath(os.fspath(left))) == os.path.normcase(os.path.realpath(os.fspath(right)))


def link_free(path) -> bool:
    """True when no existing component is a symbolic link or reparse point.

    Unlike ``Path.resolve() == path`` this never treats an 8.3 short name as a
    redirect, so short installation paths stay valid.
    """
    absolute = Path(os.path.abspath(os.fspath(path)))
    for part in (absolute, *absolute.parents):
        try:
            info = part.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & FILE_ATTRIBUTE_REPARSE_POINT:
            return False
    return True
