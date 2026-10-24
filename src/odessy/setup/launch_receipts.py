"""Bounded launch handoff receipts, never installation authorization."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import tempfile
from .maintenance import guard, ManagedInstallation, read_json

_ID = re.compile(r'[a-f0-9]{32}')

def _context(root, launch_id):
    if not isinstance(launch_id, str) or not _ID.fullmatch(launch_id):
        raise ValueError('invalid launch identifier')
    root = guard(root)
    record = ManagedInstallation(root).record()
    version = record.get('active')
    if not isinstance(version, str) or not _ID.fullmatch(version):
        raise ValueError('the installation has no active version')
    return root, record, version

def _write(path, value):
    fd, name = tempfile.mkstemp(prefix='.receipt-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream)
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)

def prepare(root, launch_id):
    root, record, version = _context(root, launch_id)
    directory = guard(root / 'launch-receipts')
    directory.mkdir(exist_ok=True)
    path = guard(directory / (launch_id + '.json'))
    if path.exists(): raise ValueError('launch identifier already exists')
    files=[]
    for candidate in directory.glob('*.json'):
        if not _ID.fullmatch(candidate.stem):continue
        try:
            candidate=guard(candidate)
            if candidate.stat().st_size>4096:continue
            stored=read_json(candidate)
            if (stored.get('root')==str(root) and stored.get('installation_id')==record.get('id')
                    and stored.get('launch_id')==candidate.stem):files.append(candidate)
        except (OSError,ValueError,AttributeError):continue
    files.sort(key=lambda p:p.stat().st_mtime_ns)
    for old in files[:-63]: guard(old).unlink()
    _write(path, {'root': str(root), 'installation_id': record.get('id'),
                  'version': version, 'launch_id': launch_id, 'state': 'pending'})
    return launch_id

def _receipt(root, launch_id):
    root, record, version = _context(root, launch_id)
    path = guard(root / 'launch-receipts' / (launch_id + '.json'))
    if not path.is_file() or path.stat().st_size > 4096:
        raise ValueError('unknown launch identifier')
    value = read_json(path)
    if (value.get('root') != str(root) or value.get('installation_id') != record.get('id')
            or value.get('version') != version or value.get('launch_id') != launch_id):
        raise ValueError('obsolete launch identifier')
    if value.get('state') not in ('pending', 'ready', 'configuration_required', 'failed'):
        raise ValueError('invalid launch receipt')
    return path, value

def acknowledge(root, launch_id, state):
    if state not in ('ready', 'configuration_required', 'failed'):
        raise ValueError('invalid launch state')
    path, value = _receipt(root, launch_id)
    if value['state'] not in ('pending', state):
        raise ValueError('launch already acknowledged')
    value['state'] = state
    _write(path, value)
    return {'launch_id': launch_id, 'state': state}

def status(root, launch_id):
    _, value = _receipt(root, launch_id)
    return {'launch_id': launch_id, 'state': value['state']}


def pending(root, version=None):
    root = guard(root)
    record = ManagedInstallation(root).record()
    if version is not None and version != record.get('active'):
        return {'launch_ids': []}
    identifiers = []
    for path in sorted((root/'launch-receipts').glob('*.json')):
        if len(identifiers) >= 64: break
        if not _ID.fullmatch(path.stem): continue
        try:
            _, value = _receipt(root, path.stem)
            if value['state'] == 'pending': identifiers.append(path.stem)
        except (ValueError, OSError):
            continue
    return {'launch_ids': identifiers}
