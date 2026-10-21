"""Bounded installer subprocesses with ownership of Windows child processes."""
from __future__ import annotations
import ctypes
import os
import signal
import subprocess
import time
from ctypes import wintypes
from pathlib import Path


class ProcessTree:
    def __init__(self):
        self.handle = None
        if os.name != 'nt':
            return
        class BasicLimits(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong), ('PerJobUserTimeLimit', ctypes.c_longlong),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]
        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ('ReadOperationCount', 'WriteOperationCount',
                'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IoCounters),
                        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE,wintypes.UINT]
        self.kernel.TerminateJobObject.restype = wintypes.BOOL
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD,ctypes.c_void_p]
        self.kernel.QueryInformationJobObject.restype = wintypes.BOOL
        class Accounting(ctypes.Structure):
            _fields_ = [(name,ctypes.c_longlong) for name in ('TotalUserTime','TotalKernelTime','ThisPeriodTotalUserTime','ThisPeriodTotalKernelTime')] + [
                (name,wintypes.DWORD) for name in ('TotalPageFaultCount','TotalProcesses','ActiveProcesses','TotalTerminatedProcesses')]
        self.accounting_type=Accounting
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits(); limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error()); self.close(); raise error

    def attach(self, process):
        if os.name == 'nt' and not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle is not None:
            try:
                if not self.kernel.TerminateJobObject(self.handle,1):raise ctypes.WinError(ctypes.get_last_error())
                deadline=time.monotonic()+5
                while True:
                    info=self.accounting_type()
                    if not self.kernel.QueryInformationJobObject(self.handle,1,ctypes.byref(info),ctypes.sizeof(info),None):
                        raise ctypes.WinError(ctypes.get_last_error())
                    if not info.ActiveProcesses:break
                    if time.monotonic()>=deadline:raise ValueError('Owned child processes did not stop within five seconds.')
                    time.sleep(.01)
            finally:
                self.kernel.CloseHandle(self.handle); self.handle = None


def run_application(command: list[str], *, cwd: Path, env: dict, label: str, timeout: float) -> str:
    """No stdin approvals; never terminate by process name or by a saved PID."""
    tree = ProcessTree()
    process = None
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), start_new_session=os.name != 'nt')
        tree.attach(process)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise ValueError(f'{label} exceeded {timeout:g} seconds; owned application/tool processes stopped.') from error
        if process.returncode:
            raise ValueError(f'{label} failed (exit {process.returncode}): ' + (stderr or stdout)[-3000:])
        if len(stdout) > 256 * 1024 or len(stderr) > 256 * 1024:
            raise ValueError(label + ' returned unexpectedly large diagnostic output.')
        return stdout
    finally:
        # Closing the job also handles descendants if the parent already exited.
        tree.close()
        if process is not None:
            if os.name != 'nt':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
