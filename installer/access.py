"""Access checks for private runtimes, without elevation or automatic ACL changes."""
from __future__ import annotations
import os
import stat
from pathlib import Path


def check_runtime_access(runtime: Path):
    for relative in ('python-runtime/python.exe', 'node-runtime/node.exe'):
        path = runtime / relative
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode): raise ValueError('运行环境文件不是普通文件：' + str(path))
            with path.open('rb') as stream: stream.read(1)
        except PermissionError as error:
            raise ValueError('当前账户无法读取或执行旧运行环境：' + str(path) +
                             '。请修复此安装的账户权限；程序不会自动提权，也不会启动模型服务。') from error
        except FileNotFoundError as error:
            raise ValueError('运行环境文件缺失：' + str(path) + '。请检查安装是否完整；文件缺失与权限不足是不同问题。') from error


def is_elevated():
    if os.name != 'nt': return False
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin())
