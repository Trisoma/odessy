"""Cross-process microphone lease, released by the OS after process termination.

Never remove a lock file to recover it. A diagnostic helper and coordinator must
not compete for the same microphone, even if the OS permits shared recording.
"""
import os
from pathlib import Path
import stat
import threading

def lease_path():return Path.home()/'.odessy'/'microphone.lock'

class MicrophoneLease:
    def __init__(self):self.fd=None;self.lock=threading.Lock()
    def acquire(self):
        path=lease_path();path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if path.is_symlink():raise RuntimeError('Microphone lock is not an owned regular file')
        fd=os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            info=os.fstat(fd);current=path.lstat()
            if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino)!=(current.st_dev,current.st_ino):raise RuntimeError('Microphone lock changed')
            if hasattr(os,'getuid') and info.st_uid!=os.getuid():raise RuntimeError('Microphone lock belongs to another user')
            if info.st_size==0:os.write(fd,b'0')
            os.lseek(fd,0,os.SEEK_SET)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.fd=fd
        except OSError:
            os.close(fd);raise RuntimeError('Another Odessy session or diagnostic owns the microphone. Stop it before recording.') from None
        except BaseException:os.close(fd);raise
    def close(self):
        with self.lock:fd,self.fd=self.fd,None
        if fd is not None:os.close(fd)

class LeasedStream:
    def __init__(self,stream):
        self.stream=stream;self.lease=MicrophoneLease();self.lock=threading.Lock();self.opening=False;self.closed=False
    def start(self):
        with self.lock:
            if self.closed:raise RuntimeError('Capture cancelled before startup')
            self.opening=True
        try:
            self.lease.acquire();self.stream.start()
            if self.closed:raise RuntimeError('Capture cancelled during startup')
            return self
        except BaseException:self.close();raise
        finally:
            with self.lock:self.opening=False;closed=self.closed
            if closed:
                try:self.stream.close()
                finally:self.lease.close()
    def read(self,size):return self.stream.read(size)
    def close(self):
        with self.lock:self.closed=True;opening=self.opening
        try:self.stream.close()
        finally:
            if not opening:self.lease.close()
