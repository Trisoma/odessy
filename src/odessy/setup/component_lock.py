"""Owned source-writer journal plus an OS lock; crash recovery never replays saves."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import uuid

MAGIC='odessy-component-writer-v1'

def _path():
    from .configured import desktop_state_file
    return desktop_state_file().parent/'.component-settings.lock'

def _write(fd, value):
    data=json.dumps(value,sort_keys=True).encode()
    os.lseek(fd,0,os.SEEK_SET);os.write(fd,data);os.ftruncate(fd,len(data));os.fsync(fd)

@contextmanager
def locked():
    path=_path()
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.is_symlink():raise ValueError('Unowned settings lock requires manual inspection.')
    flags=os.O_RDWR|getattr(os,'O_NOFOLLOW',0)
    created=False
    try:
        fd=os.open(path,flags|os.O_CREAT|os.O_EXCL,0o600);created=True
    except FileExistsError:
        fd=os.open(path,flags)
    acquired=False
    try:
        info=os.fstat(fd);current=path.lstat()
        if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino)!=(current.st_dev,current.st_ino):
            raise ValueError('Settings lock ownership changed; no recovery performed.')
        if hasattr(os,'getuid') and info.st_uid!=os.getuid():raise ValueError('Settings lock belongs to another user.')
        if created:_write(fd,{'owner':MAGIC,'state':'idle','operation':uuid.uuid4().hex})
        try:
            os.lseek(fd,0,os.SEEK_SET)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            acquired=True
        except OSError:
            raise ValueError('Settings writer is active. Wait before inspecting or recovering.') from None
        os.lseek(fd,0,os.SEEK_SET);raw=os.read(fd,4097)
        try:value=json.loads(raw)
        except (ValueError,UnicodeDecodeError):raise ValueError('Unowned legacy settings lock requires manual inspection; it was not removed.') from None
        if (len(raw)>4096 or not isinstance(value,dict) or value.get('owner')!=MAGIC or
                value.get('state') not in ('idle','pending') or not isinstance(value.get('operation'),str)):
            raise ValueError('Unowned settings lock requires manual inspection; it was not removed.')
        yield fd,value
    finally:
        if acquired:
            if os.name=='nt':
                import msvcrt
                os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)

@contextmanager
def writer_lock():
    with locked() as (fd,value):
        if value['state']=='pending':raise ValueError('Inspect and review the interrupted settings writer before saving.')
        _write(fd,{'owner':MAGIC,'state':'pending','operation':uuid.uuid4().hex,'pid':os.getpid()})
        yield
        # Exceptions and forced termination deliberately retain pending state.
        _write(fd,{'owner':MAGIC,'state':'idle','operation':uuid.uuid4().hex})

def inspect_or_recover(action,confirm,load):
    from odessy.application.capabilities import digest
    if not _path().exists():return {'recoverable':False,'status':'No interrupted settings writer.'}
    try:
        with locked() as (fd,value):
            current=load()
            plan=digest({'writer':value,'revision':current['revision']})
            if action=='lock-recover':
                if value['state']!='pending' or confirm!=plan:
                    raise ValueError('Inspect and review the current writer state before recovery.')
                _write(fd,{'owner':MAGIC,'state':'idle','operation':uuid.uuid4().hex})
                return {'recoverable':False,'status':'Recovery acknowledged the current saved pointer. No operation replayed or files removed.'}
            return {'recoverable':value['state']=='pending','plan_id':plan,
                    'status':'Interrupted writer; review the saved state before recovery.' if value['state']=='pending' else 'Writer is idle.',
                    'configuration':current.get('configuration'),'revision':current['revision']}
    except (ValueError,OSError) as error:
        if action=='lock-recover':raise
        return {'recoverable':False,'status':str(error)}

def summary():
    if not _path().exists():return None
    try:
        with locked() as (_,value):
            return {'pending':value['state']=='pending','status':'Interrupted settings writer. Inspect the saved state before another save.' if value['state']=='pending' else 'Writer is idle.'}
    except (ValueError,OSError) as error:
        active='writer is active' in str(error)
        return {'pending':not active,'busy':active,'status':str(error)}
