"""Explicit portable CPython inventory; external site-packages never bundled."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil


def inventory(root):
    root=Path(root).resolve()
    executable='python.exe' if (root/'python.exe').is_file() else 'bin/python3'
    if not (root/executable).is_file():raise ValueError('portable runtime lacks Python executable')
    if not any((root/name).is_file() for name in ('LICENSE','LICENSE.txt')):raise ValueError('portable runtime license is required')
    files={}
    for path in root.rglob('*'):
        relative=path.relative_to(root)
        if any(part in ('site-packages','__pycache__','test','tests','.git') for part in relative.parts):continue
        if relative.parts[0] not in ('Lib','lib','DLLs','include','Include','libs','bin') and len(relative.parts)>1:continue
        if not path.is_file():continue
        resolved=path.resolve()
        if not resolved.is_relative_to(root):raise ValueError('runtime link escapes its portable directory')
        h=hashlib.sha256()
        with resolved.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
        files[relative.as_posix()]=h.hexdigest()
    return {'executable':executable,'files':files}


def copy_owned(source,destination,expected):
    source,destination=Path(source),Path(destination)
    if inventory(source)!=expected:raise ValueError('runtime changed after review')
    destination.mkdir(parents=True,exist_ok=True)
    for name,sha in expected['files'].items():
        path=destination/name
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:raise ValueError('owned runtime was modified')
        else:shutil.copy2((source/name).resolve(),path)
    if inventory(destination)!=expected:raise ValueError('runtime copy verification failed')
    return destination/expected['executable']
