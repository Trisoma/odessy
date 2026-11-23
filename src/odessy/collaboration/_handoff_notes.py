"""Create handoff notes relative to directory handles, without following links."""
from contextlib import ExitStack
import os


HANDOFF_DIR = '.odessy-handoff'


def write_note(worktree, name, text):
    with ExitStack() as stack:
        if os.name == 'nt':
            fd = _open_windows_note(worktree, name, stack)
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            root = os.open(worktree, flags)
            stack.callback(os.close, root)
            try:
                os.mkdir(HANDOFF_DIR, 0o700, dir_fd=root)
            except FileExistsError:
                pass
            directory = os.open(HANDOFF_DIR, flags, dir_fd=root)
            stack.callback(os.close, directory)
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)


def _open_windows_note(worktree, name, stack):
    import ctypes as ct
    from ctypes import wintypes as win
    import msvcrt

    class UnicodeString(ct.Structure):
        _fields_ = [('length', win.USHORT), ('capacity', win.USHORT), ('buffer', win.LPWSTR)]

    class ObjectAttributes(ct.Structure):
        _fields_ = [('length', win.ULONG), ('root', win.HANDLE),
                    ('name', ct.POINTER(UnicodeString)), ('attributes', win.ULONG),
                    ('security_descriptor', ct.c_void_p), ('security_qos', ct.c_void_p)]

    class IoStatusBlock(ct.Structure):
        _fields_ = [('status_or_pointer', ct.c_void_p), ('information', ct.c_size_t)]

    class FileAttributeTagInfo(ct.Structure):
        _fields_ = [('attributes', win.DWORD), ('tag', win.DWORD)]

    kernel = ct.WinDLL('kernel32', use_last_error=True)
    create_root = kernel.CreateFileW
    create_root.argtypes = (win.LPCWSTR, win.DWORD, win.DWORD, ct.c_void_p,
                            win.DWORD, win.DWORD, win.HANDLE)
    create_root.restype = win.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (win.HANDLE,)
    close.restype = win.BOOL
    get_info = kernel.GetFileInformationByHandleEx
    get_info.argtypes = (win.HANDLE, ct.c_int, ct.c_void_p, win.DWORD)
    get_info.restype = win.BOOL

    share_read, share_write = 1, 2
    file_traverse, read_attributes = 0x20, 0x80
    open_existing = 3
    backup_semantics, open_reparse_point = 0x02000000, 0x00200000
    root = create_root(str(worktree), file_traverse | read_attributes,
                       share_read | share_write, None, open_existing,
                       backup_semantics | open_reparse_point, None)
    if root == win.HANDLE(-1).value:
        raise ct.WinError(ct.get_last_error())
    stack.callback(close, root)
    file_attribute_tag_info = 9
    directory_attribute, reparse_attribute = 0x10, 0x400
    info = FileAttributeTagInfo()
    if not get_info(root, file_attribute_tag_info, ct.byref(info), ct.sizeof(info)):
        raise ct.WinError(ct.get_last_error())
    if not info.attributes & directory_attribute or info.attributes & reparse_attribute:
        raise OSError('handoff worktree must be a real directory')

    # Win32 CreateFileW cannot open a name relative to a directory handle.
    native = ct.WinDLL('ntdll')
    create = native.NtCreateFile
    create.argtypes = (ct.POINTER(win.HANDLE), win.ULONG, ct.POINTER(ObjectAttributes),
                       ct.POINTER(IoStatusBlock), ct.c_void_p, win.ULONG,
                       win.ULONG, win.ULONG, win.ULONG, ct.c_void_p, win.ULONG)
    create.restype = win.LONG
    to_winerror = native.RtlNtStatusToDosError
    to_winerror.argtypes = (win.LONG,)
    to_winerror.restype = win.ULONG

    case_insensitive, dont_reparse = 0x40, 0x1000
    synchronize, synchronous_io = 0x100000, 0x20
    directory_file, non_directory_file = 1, 0x40
    create_new, open_or_create = 2, 3
    normal_attribute, generic_write = 0x80, 0x40000000

    def open_relative(parent, name, access, share, disposition, options):
        buffer = ct.create_unicode_buffer(name)
        capacity = ct.sizeof(buffer)
        path = UnicodeString(capacity - ct.sizeof(win.WCHAR), capacity,
                             ct.cast(buffer, win.LPWSTR))
        attributes = ObjectAttributes(ct.sizeof(ObjectAttributes), parent, ct.pointer(path),
                                      case_insensitive | dont_reparse)
        handle = win.HANDLE()
        status = create(ct.byref(handle), access | synchronize, ct.byref(attributes),
                        ct.byref(IoStatusBlock()), None, normal_attribute, share,
                        disposition, options | synchronous_io, None, 0)
        if status < 0:
            raise ct.WinError(to_winerror(status))
        return handle.value

    directory = open_relative(root, HANDOFF_DIR, file_traverse, share_read | share_write,
                              open_or_create, directory_file)
    stack.callback(close, directory)
    handle = open_relative(directory, name, generic_write, 0, create_new, non_directory_file)
    try:
        return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except OSError:
        close(handle)
        raise
