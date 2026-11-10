"""A Windows Job Object, so the runtime's *whole* process tree has an owner.

Closing stdin lets the runtime exit gracefully, but a runtime that spawned helpers can
exit first and orphan them. A job object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``
ties every descendant to one handle: closing it terminates the tree, including
grandchildren the child never cleaned up. Where the platform cannot do this, or the
process is already inside a foreign job, ``HarnessProcess`` falls back to
``taskkill /T`` and the job is simply never created.

The module is imported only on Windows and only by the process supervisor.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _BASIC_LIMIT(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BASIC_LIMIT),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JobObject:
    """Best-effort: construction never raises; ``available`` says whether it worked."""

    def __init__(self):
        """Initialize the owned state and synchronization used by this component."""
        self._handle = None
        self.reason = ""
        if sys.platform != "win32":
            self.reason = "not windows"
            return
        try:
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
            handle = self._kernel32.CreateJobObjectW(None, None)
            if not handle:
                self.reason = f"CreateJobObjectW failed ({ctypes.get_last_error()})"
                return
            limits = _EXTENDED_LIMIT()
            limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self._kernel32.SetInformationJobObject(
                handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits), ctypes.sizeof(limits),
            ):
                self.reason = f"SetInformationJobObject failed ({ctypes.get_last_error()})"
                self._kernel32.CloseHandle(handle)
                return
            self._handle = handle
        except (AttributeError, OSError) as exc:  # pragma: no cover - platform shape
            self.reason = str(exc)

    @property
    def available(self) -> bool:
        """Report whether Windows process-tree ownership was established."""
        return self._handle is not None

    def adopt(self, pid: int) -> bool:
        """Put ``pid`` (and its future children) in the job."""
        if self._handle is None:
            return False
        handle = self._kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not handle:
            self.reason = f"OpenProcess failed ({ctypes.get_last_error()})"
            return False
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, handle):
                self.reason = f"AssignProcessToJobObject failed ({ctypes.get_last_error()})"
                return False
            return True
        finally:
            self._kernel32.CloseHandle(handle)

    def close(self) -> None:
        """Closing the handle terminates every process still in the job."""
        handle, self._handle = self._handle, None
        if handle is not None:
            self._kernel32.CloseHandle(handle)
