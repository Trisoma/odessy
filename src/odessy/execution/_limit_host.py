"""Windows resource-limit host: join a limited job, then run the command in it.

Usage: ``python -I _limit_host.py <memory_mb> <processes> <cpu_seconds> -- argv...``
(``0`` turns a limit off). The host joins the job before it starts the command, so
the command and every process it starts are limited from their first instruction;
closing the job when the host exits kills whatever is left. Stdlib only, and run
by absolute path with isolated Python.
"""

import ctypes
from ctypes import wintypes
import os
import subprocess
import sys

_KILL_ON_JOB_CLOSE = 0x2000
_ACTIVE_PROCESS = 0x0008
_PROCESS_MEMORY = 0x0100
_JOB_TIME = 0x0004


def main() -> None:
    memory_mb, processes, cpu_seconds = (int(value) for value in sys.argv[1:4])
    if sys.argv[4] != "--":
        print("limit host: expected -- before the command", file=sys.stderr, flush=True)
        os._exit(127)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                    ("max_ws", ctypes.c_size_t), ("active", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD)]

    class Limits(ctypes.Structure):
        _fields_ = [("basic", BasicLimits), ("io", ctypes.c_ulonglong * 6),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL

    limits = Limits()
    limits.basic.flags = _KILL_ON_JOB_CLOSE
    if memory_mb > 0:
        limits.basic.flags |= _PROCESS_MEMORY
        limits.process_memory = memory_mb * 1024 * 1024
    if processes > 0:
        # The host itself is one of the job's processes.
        limits.basic.flags |= _ACTIVE_PROCESS
        limits.basic.active = processes + 1
    if cpu_seconds > 0:
        limits.basic.flags |= _JOB_TIME
        limits.basic.job_time = cpu_seconds * 10_000_000  # 100 ns units
    job = kernel.CreateJobObjectW(None, None)
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        print(f"limit host: cannot create the job: {ctypes.WinError(ctypes.get_last_error())}", file=sys.stderr, flush=True)
        os._exit(127)
    if not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        print(f"limit host: cannot join the job: {ctypes.WinError(ctypes.get_last_error())}", file=sys.stderr, flush=True)
        os._exit(127)
    try:
        code = subprocess.call(sys.argv[5:])
    except OSError as error:
        print(f"limit host: cannot start the command: {error}", file=sys.stderr, flush=True)
        code = 127
    os._exit(code)


if __name__ == "__main__":
    main()
