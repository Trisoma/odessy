"""Extract verified runtime archives into a new staging directory only."""
from __future__ import annotations
import re
import os
import time
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO,Protocol
from installer.download import verified, IntegrityError

class ArchiveInfo(Protocol):
    version: str
    size_bytes: int
    sha256: str

class ArchiveError(ValueError):
    pass

def relative_member(name: str, prefix: str) -> Path | None:
    if not isinstance(name, str) or not name:
        raise ArchiveError('Invalid archive path.')
    # Windows also treats backslashes, drive letters, ADS and trailing dots specially.
    if chr(92) in name or any(ord(c) < 32 for c in name):
        raise ArchiveError('Invalid Windows archive path.')
    raw = name.rstrip('/').split('/')
    if '..' in raw or name.startswith('/') or ':' in name:
        raise ArchiveError('Archive path must stay within its own root.')
    parts = PurePosixPath(name).parts
    if not parts or (prefix and parts[0] != prefix):
        raise ArchiveError('Archive member is outside the expected top-level directory.')
    tail = parts[1:] if prefix else parts
    for part in tail:
        if part.endswith((' ', '.')) or any(c in part for c in '<>"|?*'):
            raise ArchiveError('Archive contains an invalid Windows filename.')
        if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])', part.split('.')[0].rstrip(' '), re.I):
            raise ArchiveError('Archive contains a reserved Windows filename.')
    return Path(*tail) if tail else None

def _copy(source: BinaryIO, destination: Path, expected: int):
    received = 0
    with destination.open('xb') as output:
        while chunk := source.read(min(1024*1024, expected-received+1)):
            received += len(chunk)
            if received > expected:
                raise ArchiveError('Archive member exceeded its declared size.')
            output.write(chunk)
    if received != expected:
        raise ArchiveError('Archive member was truncated.')

def unpack(archive_path: Path, artifact: ArchiveInfo, target: Path, *, kind: str,
           prefix: str, unpacked_bytes: int, preserve_timestamps: bool = False) -> None:
    if target.exists() or target.is_symlink():
        raise ArchiveError('Extraction target must not already exist.')
    if type(unpacked_bytes) is not int or unpacked_bytes <= 0:
        raise ArchiveError('A positive measured extraction size is required.')
    if kind not in {'zip','tar.gz'} or not (prefix in {'python','node-v'+artifact.version+'-win-x64'} or (prefix in {'tools',''} and kind=='zip')):
        raise ArchiveError('Unsupported runtime archive format or root.')
    if not verified(archive_path, artifact):
        raise IntegrityError('Archive size or hash differs from the release manifest.')
    try:
        archive = zipfile.ZipFile(archive_path) if kind == 'zip' else tarfile.open(archive_path, 'r:gz')
    except (zipfile.BadZipFile, tarfile.TarError) as error:
        raise ArchiveError('Verified download is not a readable runtime archive.') from error
    with archive:
        entries = archive.infolist() if kind == 'zip' else archive.getmembers()
        if len(entries) > 100000:
            raise ArchiveError('Too many archive entries.')
        seen, planned, total = set(), [], 0
        for entry in entries:
            name = entry.filename if kind == 'zip' else entry.name
            path = relative_member(name, prefix)
            directory = entry.is_dir() if kind == 'zip' else entry.isdir()
            if kind == 'zip':
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if mode not in {0,stat.S_IFREG,stat.S_IFDIR} or entry.external_attr & 0x400:
                    raise ArchiveError('Links or special archive entries are not accepted.')
                size = entry.file_size
            else:
                if not (entry.isfile() or directory):
                    raise ArchiveError('Links or special archive entries are not accepted.')
                size = entry.size
            if path is None:
                if not directory:
                    raise ArchiveError('Archive root must be a directory.')
                continue
            key = path.as_posix().casefold()
            if key in seen:
                raise ArchiveError('Duplicate or case-colliding archive paths.')
            seen.add(key)
            total += size
            if total > unpacked_bytes:
                raise ArchiveError('Archive exceeds measured expanded size.')
            planned.append((entry,path,directory,size))
        if total != unpacked_bytes:
            raise ArchiveError('Archive expanded size differs from release metadata.')
        # Validate the complete member list before creating the extraction target.
        target.mkdir(parents=False)
        for entry,path,directory,size in planned:
            output = target/path
            if directory:
                output.mkdir(parents=True,exist_ok=True)
                continue
            output.parent.mkdir(parents=True,exist_ok=True)
            source = archive.open(entry) if kind == 'zip' else archive.extractfile(entry)
            if source is None:
                raise ArchiveError('Cannot read archive member.')
            with source:
                _copy(source,output,size)
            if preserve_timestamps:
                stamp=time.mktime(entry.date_time+(0,0,-1)) if kind=='zip' else entry.mtime
                os.utime(output,(stamp,stamp))
