"""Unicode Windows shell links via IShellLinkW, without WScript's ANSI conversion."""
from __future__ import annotations
import contextlib
import ctypes
import os
import uuid
from pathlib import Path
from ctypes import wintypes


class GUID(ctypes.Structure):
    _fields_=[('data1',wintypes.DWORD),('data2',wintypes.WORD),
              ('data3',wintypes.WORD),('data4',ctypes.c_ubyte*8)]


def _guid(value):
    return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


def _check(result):
    if result<0:
        raise OSError(f'Windows shell link operation failed (HRESULT 0x{result & 0xffffffff:08x}).')


def _call(pointer,index,types=(),args=()):
    table=ctypes.cast(pointer,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    method=ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p,*types)(table[index])
    return method(pointer,*args)


@contextlib.contextmanager
def _link():
    if os.name!='nt':raise ValueError('Shell links require Windows.')
    ole=ctypes.WinDLL('ole32')
    ole.CoInitializeEx.argtypes=[ctypes.c_void_p,wintypes.DWORD]
    ole.CoInitializeEx.restype=ctypes.c_long
    ole.CoUninitialize.argtypes=[];ole.CoUninitialize.restype=None
    ole.CoCreateInstance.argtypes=[ctypes.POINTER(GUID),ctypes.c_void_p,wintypes.DWORD,
                                  ctypes.POINTER(GUID),ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype=ctypes.c_long
    initialized=ole.CoInitializeEx(None,2)  # Apartment threaded, on the calling worker.
    # An existing COM apartment can also create ShellLink; leave its lifetime alone.
    if (initialized & 0xffffffff)!=0x80010106:_check(initialized)
    link=ctypes.c_void_p();persist=ctypes.c_void_p()
    try:
        clsid=_guid('00021401-0000-0000-c000-000000000046')
        iid=_guid('000214f9-0000-0000-c000-000000000046')
        _check(ole.CoCreateInstance(ctypes.byref(clsid),None,1,ctypes.byref(iid),ctypes.byref(link)))
        iid_file=_guid('0000010b-0000-0000-c000-000000000046')
        _check(_call(link,0,(ctypes.POINTER(GUID),ctypes.POINTER(ctypes.c_void_p)),
                     (ctypes.byref(iid_file),ctypes.byref(persist))))
        yield link,persist
    finally:
        if persist:_call(persist,2)
        if link:_call(link,2)
        if initialized>=0:ole.CoUninitialize()


def _get(link,index):
    buffer=ctypes.create_unicode_buffer(32768)
    _check(_call(link,index,(wintypes.LPWSTR,ctypes.c_int),(buffer,len(buffer))))
    return buffer.value


def read(path: Path) -> dict:
    """Read stored fields without resolving or launching the shortcut."""
    with _link() as (link,persist):
        _check(_call(persist,5,(wintypes.LPCWSTR,wintypes.DWORD),(str(path),0)))
        target=ctypes.create_unicode_buffer(32768)
        _check(_call(link,3,(wintypes.LPWSTR,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD),
                     (target,len(target),None,4)))  # SLGP_RAWPATH, no link resolution.
        return dict(target=target.value,arguments=_get(link,10),working=_get(link,8))


def write(path: Path,target: Path,arguments: str,working: Path):
    """Write only a caller-owned staging path; publishing remains exclusive."""
    with _link() as (link,persist):
        for index,value in ((20,str(target)),(11,arguments),(9,str(working)),(7,'Start Odessy')):
            _check(_call(link,index,(wintypes.LPCWSTR,),(value,)))
        _check(_call(link,17,(wintypes.LPCWSTR,ctypes.c_int),(str(target),0)))
        _check(_call(persist,6,(wintypes.LPCWSTR,wintypes.BOOL),(str(path),True)))
