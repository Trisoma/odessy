"""Handle-anchored file replacement for Windows checkpoint restores.

POSIX uses ``openat``/``O_NOFOLLOW`` + ``renameat2`` to make a restore immune to
path-replacement (TOCTOU) attacks: an attacker swapping an ancestor directory
for a symlink between validation and write must not redirect the restore
outside the workspace. Windows Python exposes no ``dir_fd``/``O_NOFOLLOW``, so
this module binds the Win32/NT primitives directly:

* ``NtCreateFile`` with ``RootDirectory`` opens each path component relative to
  the already-open parent handle, with ``FILE_OPEN_REPARSE_POINT`` so a junction
  or symlink is opened as itself and can be detected instead of followed.
* ``GetFileInformationByHandle`` rejects any component carrying
  ``FILE_ATTRIBUTE_REPARSE_POINT``.
* ``NtSetInformationFile(FileRenameInformation)`` with ``RootDirectory`` set to
  the verified parent handle performs the final replace atomically, relative
  to that handle — never by re-resolving a path string.

This is deliberately a narrow, all-or-nothing primitive: it either completes
the anchored replace or raises :class:`WindowsReplaceError` before any target
byte is touched. It never silently falls back to a path-based write.

Everything is best-effort on availability: on a non-Windows host, or if the NT
bindings are unavailable, :func:`replace_anchored` raises
:class:`ReplaceUnavailable` and callers keep restoration disabled.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Sequence

__all__ = ['WindowsReplaceError', 'ReplaceUnavailable', 'replace_anchored', 'available']


class WindowsReplaceError(Exception):
    """An anchored replace was attempted and refused; no target bytes changed."""


class ReplaceUnavailable(Exception):
    """The platform or bindings cannot provide an anchored replace at all."""


# --- NT/Win32 constants ---------------------------------------------------
FILE_READ_ATTRIBUTES = 0x0080
FILE_READ_DATA = 0x0001
FILE_WRITE_DATA = 0x0002
DELETE = 0x00010000
SYNCHRONIZE = 0x00100000
FILE_SHARE_ALL = 0x00000007
FILE_OPEN = 1
FILE_CREATE = 2
FILE_DIRECTORY_FILE = 0x00000001
FILE_NON_DIRECTORY_FILE = 0x00000040
FILE_OPEN_REPARSE_POINT = 0x00200000
FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FileRenameInformation = 10  # NT enum value (user-mode FileRenameInfo is 3)
STATUS_SUCCESS = 0
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def _nt_success(status: int) -> bool:
    return (status & 0xFFFFFFFF) < 0x80000000


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [('Length', wintypes.USHORT),
                ('MaximumLength', wintypes.USHORT),
                ('Buffer', wintypes.LPWSTR)]


class _IO_STATUS_BLOCK(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [('Status', wintypes.LONG), ('Pointer', ctypes.c_void_p)]
    _anonymous_ = ('u',)
    _fields_ = [('u', _U), ('Information', ctypes.c_size_t)]


class _OBJECT_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('Length', wintypes.ULONG),
                ('RootDirectory', wintypes.HANDLE),
                ('ObjectName', ctypes.POINTER(_UNICODE_STRING)),
                ('Attributes', wintypes.ULONG),
                ('SecurityDescriptor', ctypes.c_void_p),
                ('SecurityQualityOfService', ctypes.c_void_p)]


class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    _fields_ = [('dwFileAttributes', wintypes.DWORD),
                ('ftCreationTime', wintypes.FILETIME),
                ('ftLastAccessTime', wintypes.FILETIME),
                ('ftLastWriteTime', wintypes.FILETIME),
                ('dwVolumeSerialNumber', wintypes.DWORD),
                ('nFileSizeHigh', wintypes.DWORD),
                ('nFileSizeLow', wintypes.DWORD),
                ('nNumberOfLinks', wintypes.DWORD),
                ('nFileIndexHigh', wintypes.DWORD),
                ('nFileIndexLow', wintypes.DWORD)]


class _FILE_RENAME_INFORMATION(ctypes.Structure):
    _fields_ = [('ReplaceIfExists', wintypes.BOOLEAN),
                ('RootDirectory', wintypes.HANDLE),
                ('FileNameLength', wintypes.DWORD),
                ('FileName', wintypes.WCHAR * 1)]


_BINDINGS: dict[str, object] | None = None
_BINDING_ERROR = ''


def _bindings() -> dict[str, object]:
    """Bind the NT/Win32 functions once; raise ReplaceUnavailable if not possible."""
    global _BINDINGS, _BINDING_ERROR
    if _BINDINGS is not None:
        return _BINDINGS
    if os.name != 'nt':
        raise ReplaceUnavailable('not a Windows host')
    try:
        ntdll = ctypes.WinDLL('ntdll', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

        nt_create = ntdll.NtCreateFile
        nt_create.argtypes = [
            ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, ctypes.POINTER(_OBJECT_ATTRIBUTES),
            ctypes.POINTER(_IO_STATUS_BLOCK), ctypes.POINTER(wintypes.LARGE_INTEGER),
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD]
        nt_create.restype = wintypes.LONG

        nt_set_info = ntdll.NtSetInformationFile
        nt_set_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IO_STATUS_BLOCK),
                                ctypes.c_void_p, wintypes.ULONG, ctypes.c_int]
        nt_set_info.restype = wintypes.LONG

        get_info = kernel32.GetFileInformationByHandle
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION)]
        get_info.restype = wintypes.BOOL

        write_file = kernel32.WriteFile
        write_file.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        write_file.restype = wintypes.BOOL

        flush = kernel32.FlushFileBuffers
        flush.argtypes = [wintypes.HANDLE]
        flush.restype = wintypes.BOOL

        read_file = kernel32.ReadFile
        read_file.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        read_file.restype = wintypes.BOOL

        close = kernel32.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL

        _BINDINGS = {'nt_create': nt_create, 'nt_set_info': nt_set_info,
                     'get_info': get_info, 'write': write_file, 'read': read_file,
                     'flush': flush, 'close': close}
        return _BINDINGS
    except (OSError, AttributeError) as exc:
        _BINDING_ERROR = f'{type(exc).__name__}: {exc}'
        raise ReplaceUnavailable(_BINDING_ERROR) from None


def available() -> bool:
    """Whether handle-anchored replacement can be attempted on this host."""
    try:
        _bindings()
        return True
    except ReplaceUnavailable:
        return False


def _close(handle: int) -> None:
    if handle and handle != _INVALID_HANDLE:
        try:
            _bindings()['close'](wintypes.HANDLE(handle))  # type: ignore[index]
        except Exception:
            pass


def _nt_open(parent: int | None, name: str, *, desired: int, disposition: int,
             options: int) -> tuple[int, int]:
    """Open a file/directory by name relative to ``parent`` (or by NT path)."""
    b = _bindings()
    handle = wintypes.HANDLE()
    io = _IO_STATUS_BLOCK()
    name_buf = ctypes.create_unicode_buffer(name)
    unicode_name = _UNICODE_STRING(len(name) * 2, len(name) * 2 + 2,
                                   ctypes.cast(name_buf, wintypes.LPWSTR))
    oa = _OBJECT_ATTRIBUTES(
        ctypes.sizeof(_OBJECT_ATTRIBUTES),
        wintypes.HANDLE(parent) if parent else wintypes.HANDLE(),
        ctypes.pointer(unicode_name), 0, None, None)
    status = b['nt_create'](  # type: ignore[index]
        ctypes.byref(handle), desired, ctypes.byref(oa), ctypes.byref(io),
        None, 0, FILE_SHARE_ALL, disposition, options, None, 0)
    if not _nt_success(status):
        raise WindowsReplaceError(f'open failed ntstatus=0x{status & 0xFFFFFFFF:x}: {name!r}')
    value = handle.value if hasattr(handle, 'value') else int(handle)
    return int(status), int(value)


def _is_reparse(handle: int) -> bool:
    info = _BY_HANDLE_FILE_INFORMATION()
    ok = _bindings()['get_info'](wintypes.HANDLE(handle), ctypes.byref(info))  # type: ignore[index]
    if not ok:
        raise WindowsReplaceError('could not inspect directory attributes')
    return bool(info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)


def _open_ancestors(root_path: str, parts: Sequence[str]) -> list[int]:
    """Open root then each directory component, anchored and reparse-free."""
    handles: list[int] = []
    try:
        nt_root = '\\??\\' + str(Path(root_path).resolve())
        _status, root_h = _nt_open(
            None, nt_root,
            desired=FILE_READ_ATTRIBUTES | SYNCHRONIZE,
            disposition=FILE_OPEN,
            options=FILE_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT | FILE_SYNCHRONOUS_IO_NONALERT)
        if _is_reparse(root_h):
            raise WindowsReplaceError('workspace root is a reparse point')
        handles.append(root_h)
        parent = root_h
        for component in parts:
            if component in ('', '.', '..') or '/' in component or '\\' in component:
                raise WindowsReplaceError('invalid path component')
            _status, child = _nt_open(
                parent, component,
                desired=FILE_READ_ATTRIBUTES | SYNCHRONIZE,
                disposition=FILE_OPEN,
                options=FILE_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT | FILE_SYNCHRONOUS_IO_NONALERT)
            if _is_reparse(child):
                _close(child)
                raise WindowsReplaceError(f'reparse point in path: {component!r}')
            handles.append(child)
            parent = child
        return handles
    except Exception:
        for h in reversed(handles):
            _close(h)
        raise


STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034


def _anchored_hash(parent: int, name: str) -> str | None:
    """Hash a file by name relative to an already-verified parent handle.

    Returns None when the file does not exist. Opens with
    ``FILE_OPEN_REPARSE_POINT`` so a reparse point cannot be followed, and
    refuses it outright — the same rule as for directory components.
    """
    b = _bindings()
    try:
        _status, handle = _nt_open(
            parent, name,
            desired=FILE_READ_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
            disposition=FILE_OPEN,
            options=FILE_NON_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT |
                    FILE_SYNCHRONOUS_IO_NONALERT)
    except WindowsReplaceError as exc:
        if '0xc0000034' in str(exc).lower():
            return None
        raise
    try:
        if _is_reparse(handle):
            raise WindowsReplaceError(f'target is a reparse point: {name!r}')
        info = _BY_HANDLE_FILE_INFORMATION()
        if not b['get_info'](wintypes.HANDLE(handle), ctypes.byref(info)):  # type: ignore[index]
            raise WindowsReplaceError('could not read target size')
        size = (info.nFileSizeHigh << 32) | info.nFileSizeLow
        digest = hashlib.sha256()
        remaining = size
        while remaining > 0:
            chunk = min(remaining, 65536)
            buf = ctypes.create_string_buffer(chunk)
            got = wintypes.DWORD(0)
            if not b['read'](wintypes.HANDLE(handle), buf, chunk,  # type: ignore[index]
                             ctypes.byref(got), None) or got.value == 0:
                raise WindowsReplaceError('could not read target for verification')
            digest.update(buf.raw[:got.value])
            remaining -= got.value
        return digest.hexdigest()
    finally:
        _close(handle)


def replace_anchored(root_path: str, relative_path: str, data: bytes,
                     expected_sha256: str | None = None) -> None:
    """Atomically replace ``relative_path`` under ``root_path`` with ``data``.

    Every directory component is opened relative to the verified parent handle
    and rejected if it is a junction/symlink; the final replace is performed by
    ``NtSetInformationFile`` relative to the verified final-directory handle.
    No path string is re-resolved after validation, so a concurrent directory
    swap cannot redirect the write outside the workspace.

    Raises :class:`ReplaceUnavailable` when the platform lacks the primitives,
    or :class:`WindowsReplaceError` if any check fails (target untouched).
    """
    b = _bindings()
    parts = [p for p in Path(relative_path).parts if p not in ('.', '')]
    if not parts or '..' in parts:
        raise WindowsReplaceError('relative_path must stay within the workspace root')
    final_name = parts[-1]
    if '/' in final_name or '\\' in final_name:
        raise WindowsReplaceError('invalid final path component')

    handles = _open_ancestors(root_path, parts[:-1])
    parent = handles[-1]
    temp_name = '.ckpt-' + uuid.uuid4().hex
    temp_handle = 0
    try:
        # Re-check the target through the anchored handle, closing the window
        # between the caller's approval hash check and this atomic replace.
        if expected_sha256 is not None:
            observed = _anchored_hash(parent, final_name)
            if observed != expected_sha256:
                raise WindowsReplaceError('target changed after approval')
        # Create the staged file relative to the verified parent handle.
        _status, temp_handle = _nt_open(
            parent, temp_name,
            desired=FILE_WRITE_DATA | FILE_READ_ATTRIBUTES | DELETE | SYNCHRONIZE,
            disposition=FILE_CREATE,
            options=FILE_NON_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT |
                    FILE_SYNCHRONOUS_IO_NONALERT)
        if data:
            buf = ctypes.create_string_buffer(data)
            written = wintypes.DWORD(0)
            if not b['write'](wintypes.HANDLE(temp_handle), buf, len(data),  # type: ignore[index]
                              ctypes.byref(written), None) or written.value != len(data):
                raise WindowsReplaceError('staged write failed')
        if not b['flush'](wintypes.HANDLE(temp_handle)):  # type: ignore[index]
            raise WindowsReplaceError('staged flush failed')

        # Atomic rename relative to the verified parent handle. FileNameLength
        # is in bytes and excludes the NUL terminator.
        name_bytes = final_name.encode('utf-16-le')
        size = ctypes.sizeof(_FILE_RENAME_INFORMATION) + len(name_bytes)
        info_buf = ctypes.create_string_buffer(size)
        info = ctypes.cast(info_buf, ctypes.POINTER(_FILE_RENAME_INFORMATION)).contents
        info.ReplaceIfExists = True
        info.RootDirectory = wintypes.HANDLE(parent)
        info.FileNameLength = len(name_bytes)
        ctypes.memmove(ctypes.addressof(info_buf) +
                       _FILE_RENAME_INFORMATION.FileName.offset,
                       name_bytes, len(name_bytes))
        io = _IO_STATUS_BLOCK()
        status = b['nt_set_info'](wintypes.HANDLE(temp_handle), ctypes.byref(io),  # type: ignore[index]
                                  info_buf, size, FileRenameInformation)
        if not _nt_success(status):
            raise WindowsReplaceError(
                f'replace failed ntstatus=0x{status & 0xFFFFFFFF:x}')
        # The NT rename moves the file object to final_name but does NOT close
        # the handle. It must still be released or the replaced file stays
        # locked for later readers.
    finally:
        if temp_handle:
            _close(temp_handle)
        for h in reversed(handles):
            _close(h)
