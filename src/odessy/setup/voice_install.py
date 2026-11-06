"""Opt-in installation of verified speech components.

Three artifact kinds, all pinned by SHA-256 in a shipped manifest:

* ``file``    a single download stored under the voice root (models, voices);
* ``archive`` a pinned ZIP from which only an allow-list of members is extracted
              (the Whisper.cpp executable and the DLLs it loads);
* ``wheel``   a pinned Python distribution, cached under ``<voice>/wheels`` and
              installed into the *installed* application runtime by
              :func:`python_install`.

Existing files are never overwritten, and nothing is installed unless the
artifact is pinned with a size and digest. An unpinned entry is reported as
unavailable: the installer must never claim voice works because a download
happened to finish.

The voice root is deliberately outside the managed installation root. The
packaged installer refuses a non-empty installation folder, and speech models
are user-owned data that survive an upgrade, rollback or uninstall.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

#: Windows is the first target. Other platforms keep their existing voice setup.
SUPPORTED_PLATFORMS = ('windows',)

#: Selection fields the four required components populate.
COMPONENT_FIELDS = {
    'whisper_bin': 'Whisper.cpp executable',
    'whisper_model': 'Whisper model',
    'kokoro_model': 'Kokoro model',
    'kokoro_voices': 'Kokoro voices',
}

#: Imported by the application when voice is enabled. Missing any of these means
#: the Python side of voice is not installed, whatever the files on disk say.
RUNTIME_MODULES = ('numpy', 'onnxruntime', 'soundfile', 'sounddevice', 'kokoro_onnx')

KINDS = ('file', 'archive', 'wheel')
MAX_ARTIFACTS = 64
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024 * 1024
CHUNK = 256 * 1024
DEFAULT_TIMEOUT = 30
PROGRESS_NAME = 'voice-progress.json'
RECORD_NAME = 'voice-install.json'
WHEEL_DIR = 'wheels'
REQUIREMENTS_NAME = 'requirements-voice.txt'
PIP_TIMEOUT = 900

#: Extra free space kept after a download (OpenClaw's disk-space doctor keeps
#: headroom for writes that would otherwise fail later, quietly).
SPACE_HEADROOM = 1.25

#: Finding severities, ordered. A missing optional component is a warning; a
#: file that does not match its pin is an error, because it will not be used.
SEVERITIES = ('info', 'warning', 'error')


def _severity_rank(value):
    return SEVERITIES.index(value) if value in SEVERITIES else 0


def default_root():
    """The user-owned voice directory, computed at call time so HOME is honoured."""
    override = os.environ.get('ODESSY_VOICE_ROOT')
    if override:
        return Path(override).expanduser()
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
        return Path(base) / 'Odessy' / 'Voice'
    return Path.home() / '.local' / 'share' / 'odessy' / 'voice'


def _platform():
    import platform as _p
    return ({'Windows': 'windows', 'Darwin': 'macos'}.get(_p.system(), _p.system().lower()),
            {'amd64': 'x86_64', 'aarch64': 'arm64'}.get(_p.machine().lower(), _p.machine().lower()))


def manifest_path(payload=None):
    """A release may ship its own pinned manifest beside the payload."""
    if payload:
        candidate = Path(payload) / 'voice' / 'voice-artifacts.json'
        if candidate.is_file():
            return candidate
    return Path(__file__).with_name('voice-artifacts.json')


# --------------------------------------------------------------------- manifest

def _require_text(value, name, *, limit=4096, allow_empty=False):
    if not isinstance(value, str):
        raise ValueError(f'voice manifest: {name} must be a string')
    text = value.strip()
    if not allow_empty and not text:
        raise ValueError(f'voice manifest: {name} is required')
    if len(text) > limit:
        raise ValueError(f'voice manifest: {name} is too long')
    return text


def _require_digest(value, name, *, allow_empty=False):
    text = _require_text(value, name, limit=64, allow_empty=allow_empty).lower()
    if not text:
        return ''
    if len(text) != 64 or any(c not in '0123456789abcdef' for c in text):
        raise ValueError(f'voice manifest: {name} must be a SHA-256 hex digest')
    return text


def _safe_member(name):
    """A member name that cannot escape the voice directory."""
    text = _require_text(name, 'member', limit=260)
    if text.startswith(('/', '\\')) or ':' in text:
        raise ValueError('voice manifest: a file name must be relative')
    parts = Path(text.replace('\\', '/')).parts
    if len(parts) != 1 or parts[0] in ('..', ''):
        raise ValueError('voice manifest: a file name must be a bare name, not a path')
    return text


def load_manifest(path=None):
    """Read and validate the artifact manifest. Never trusts the file's shape."""
    target = Path(path) if path is not None else manifest_path()
    try:
        data = json.loads(target.read_text(encoding='utf-8'))
    except OSError as error:
        raise ValueError(f'voice manifest is unavailable: {error}') from None
    except json.JSONDecodeError:
        raise ValueError('voice manifest is not valid JSON') from None
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int:
        raise ValueError('voice manifest is missing its schema version')
    if data['schema_version'] != 2:
        raise ValueError('unsupported voice manifest version')
    artifacts = data.get('artifacts')
    if not isinstance(artifacts, list) or len(artifacts) > MAX_ARTIFACTS:
        raise ValueError('voice manifest has an invalid artifact list')
    seen, files, entries = set(), set(), []
    for item in artifacts:
        if not isinstance(item, dict):
            raise ValueError('voice manifest has an invalid artifact')
        kind = _require_text(item.get('kind'), 'kind', limit=16)
        if kind not in KINDS:
            raise ValueError('voice manifest names an unknown artifact kind')
        entry = {
            'id': _require_text(item.get('id'), 'id', limit=64),
            'label': _require_text(item.get('label'), 'label', limit=200),
            'kind': kind,
            'file': _safe_member(item.get('file')),
            'url': _require_text(item.get('url'), 'url', allow_empty=True),
            # An entry without a digest is valid data but not installable; the
            # refusal belongs in the plan, where the user can read the reason.
            'sha256': _require_digest(item.get('sha256'), 'sha256', allow_empty=True),
            'size_bytes': item.get('size_bytes', 0),
            'license': _require_text(item.get('license'), 'license', limit=100,
                                     allow_empty=True),
            'platform': _require_text(item.get('platform'), 'platform', limit=32),
            'architecture': _require_text(item.get('architecture'), 'architecture', limit=32),
        }
        size = entry['size_bytes']
        if type(size) is not int or size < 0 or size > MAX_ARTIFACT_BYTES:
            raise ValueError('voice manifest has an invalid size')
        if entry['id'] in seen or entry['file'] in files:
            raise ValueError('voice manifest repeats an artifact')
        if kind == 'wheel':
            if not entry['file'].endswith('.whl'):
                raise ValueError('voice manifest wheel must be a .whl file')
        else:
            entry['field'] = _require_text(item.get('field'), 'field', limit=32)
            if entry['field'] not in COMPONENT_FIELDS:
                raise ValueError('voice manifest names an unknown selection field')
        if kind == 'archive':
            members = item.get('members')
            if not isinstance(members, list) or not members or len(members) > 32:
                raise ValueError('voice manifest archive needs its member list')
            entry['members'] = [_safe_member(name) for name in members]
            entry['strip_prefix'] = _require_text(item.get('strip_prefix'),
                                                  'strip_prefix', limit=260,
                                                  allow_empty=True)
            executable = _safe_member(item.get('executable'))
            if executable not in entry['members']:
                raise ValueError('voice manifest executable must be an extracted member')
            entry['executable'] = executable
        seen.add(entry['id'])
        files.add(entry['file'])
        entries.append(entry)
    # An archive member and a plain file may not claim the same name.
    extracted = {name for entry in entries if entry['kind'] == 'archive'
                 for name in entry['members']}
    plain = {entry['file'] for entry in entries if entry['kind'] == 'file'}
    if extracted & plain:
        raise ValueError('voice manifest reuses a file name for two sources')
    return entries


def _availability(entry, platform):
    if entry['platform'] != platform:
        return f'This build is for {entry["platform"]}, not {platform}.'
    if not entry['url']:
        return 'No download is published for this component in this build.'
    scheme = urllib.parse.urlparse(entry['url']).scheme.lower()
    if scheme not in ('https', 'file'):
        return 'The published location is not an HTTPS download.'
    if not entry['sha256']:
        return 'No verified checksum is pinned for this build, so it is not offered.'
    if entry['size_bytes'] <= 0:
        return 'The download size is not published, so space cannot be checked.'
    return ''


# ------------------------------------------------------------------------- paths

def _target(root, entry):
    """Where an artifact's bytes end up, relative to the voice root."""
    if entry['kind'] == 'wheel':
        return Path(root) / WHEEL_DIR / entry['file']
    return Path(root) / entry['file']


def _present(root, entry):
    """True when every file this artifact produces already exists."""
    root = Path(root)
    if entry['kind'] == 'archive':
        recorded = {item.get('path'): item.get('sha256')
                    for item in _record(root).get('artifacts', [])
                    if isinstance(item, dict)}
        try:
            return all(recorded.get(str(root / name)) and
                       _digest_file(root / name)[0] == recorded[str(root / name)]
                       for name in entry['members'])
        except OSError:
            return False
    return _target(root, entry).is_file()


def paths(root, entries):
    """Selection fields to their paths, for the components that are usable."""
    root = Path(root)
    out = {}
    for entry in entries:
        if entry['kind'] == 'file' and (root / entry['file']).is_file():
            out[entry['field']] = str(root / entry['file'])
        elif entry['kind'] == 'archive' and (root / entry['executable']).is_file():
            out[entry['field']] = str(root / entry['executable'])
    return out


def _missing(root, entries, available_ids):
    return [entry['id'] for entry in entries
            if entry['id'] in available_ids and not _present(root, entry)]


# -------------------------------------------------------------------------- plan

def plan(payload=None, *, manifest=None, root=None):
    """What voice installation would do, including the honest refusals."""
    platform, architecture = _platform()
    entries = manifest if manifest is not None else load_manifest(manifest_path(payload))
    voice_root = Path(root) if root is not None else default_root()
    available, unavailable = [], []
    for entry in entries:
        reason = '' if platform in SUPPORTED_PLATFORMS else (
            f'Automatic voice downloads are not available on {platform} yet.'
        )
        reason = reason or _availability(entry, platform)
        if reason:
            unavailable.append({'id': entry['id'], 'label': entry['label'], 'reason': reason})
        else:
            available.append({
                'id': entry['id'],
                'label': entry['label'],
                'kind': entry['kind'],
                'field': entry.get('field', ''),
                'file': entry['file'],
                'size_bytes': entry['size_bytes'],
                'license': entry['license'],
                'url': entry['url'],
                'sha256': entry['sha256'],
                'installed': _present(voice_root, entry),
            })
    pending = [item for item in available if not item['installed']]
    total = sum(item['size_bytes'] for item in pending)
    try:
        free = shutil.disk_usage(voice_root).free
    except OSError:
        free = None  # Unmeasurable: say nothing rather than invent a number.
    headroom = ''
    if free is not None and total:
        if free < total:
            headroom = f'{total} bytes are required and {free} are free.'
        elif free < total * SPACE_HEADROOM:
            headroom = f'only {max(0, free - total)} bytes would remain free after the download.'
    return {
        'platform': platform,
        'architecture': architecture,
        'root': str(voice_root),
        'available': available,
        'unavailable': unavailable,
        'pending': pending,
        'total_bytes': total,
        'free_bytes': free,
        'space_warning': headroom,
        'nothing_to_do': bool(available) and total == 0,
        'requires_internet': bool(available) and total > 0,
        'installable': bool(available),
        'plan_id': _plan_id(available),
        'manifest_path': str(manifest_path(payload)),
    }


def _plan_id(available):
    digest = hashlib.sha256()
    for item in sorted(available, key=lambda value: value['id']):
        digest.update(f'{item["id"]}:{item["sha256"]}:{item["size_bytes"]};'.encode())
    return digest.hexdigest()[:32] if available else ''


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.partial')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def _progress(root, state, *, message, downloaded=0, total=0, current=''):
    try:
        _write_json(Path(root) / PROGRESS_NAME, {
            'state': state, 'message': message, 'current': current,
            'downloaded_bytes': downloaded, 'total_bytes': total,
            'updated_at': time.time(),
        })
    except OSError:
        pass  # Progress is observational; it must never fail an installation.


def _digest_file(path, *, cancelled=None):
    digest = hashlib.sha256()
    size = 0
    with open(path, 'rb') as handle:
        while True:
            if cancelled and cancelled():
                raise _Cancelled()
            block = handle.read(CHUNK)
            if not block:
                return digest.hexdigest(), size
            size += len(block)
            digest.update(block)


class _Cancelled(Exception):
    pass


# ----------------------------------------------------------------------- status

def _record(root):
    path = Path(root) / RECORD_NAME
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _finding(check, severity, message, path='', fix_hint='', *, ready=False):
    return {'check_id': f'setup/voice/{check}', 'severity': severity, 'ready': ready,
            'message': message, 'path': str(path), 'fix_hint': fix_hint}


def status(payload=None, *, manifest=None, root=None, install_root=None):
    """Report readiness as structured findings, one per component and one for Python.

    Each finding carries a stable `check_id`, a severity and a remedy, so
    "voice needs attention" is auditable instead of a sentence. A missing
    optional component is a warning; a file that does not match its recorded
    digest is an error, because it will not be used.
    """
    platform, _ = _platform()
    entries = manifest if manifest is not None else load_manifest(manifest_path(payload))
    voice_root = Path(root) if root is not None else default_root()
    recorded = {item.get('path'): item.get('sha256')
                for item in _record(voice_root).get('artifacts', [])
                if isinstance(item, dict)}
    components, missing, findings = [], [], []
    for entry in entries:
        if entry['kind'] == 'wheel':
            continue
        affected = entry['members'] if entry['kind'] == 'archive' else [entry['file']]
        target = voice_root / (entry['executable'] if entry['kind'] == 'archive'
                               else entry['file'])
        present = all((voice_root / name).is_file() for name in affected)
        verified = present
        for name in affected:
            path = voice_root / name
            pinned = recorded.get(str(path))
            if pinned is None:
                verified = False
                continue
            try:
                if _digest_file(path)[0] != pinned:
                    verified = False
            except OSError:
                verified = False
        state = 'missing' if not present else ('ready' if verified else 'unverified')
        check = f'component/{entry["id"]}'
        if state == 'ready':
            severity, message, hint = ('info',
                                       f'{entry["label"]} is present and verified.', '')
        elif state == 'unverified':
            severity = 'error'
            message = (f'{entry["label"]} is present but does not match the digest recorded '
                       'when it was installed, so it is not trusted.')
            hint = 'Download the component again, or remove the file and retry.'
        else:
            severity, message = 'warning', f'{entry["label"]} is missing.'
            hint = 'Download voice components, or select an existing file in setup.'
        components.append({'id': entry['id'], 'label': entry['label'],
                           'field': entry['field'], 'path': str(target), 'state': state,
                           'severity': severity, 'check_id': f'setup/voice/{check}',
                           'message': message, 'fix_hint': hint})
        findings.append(_finding(check, severity, message, target, hint,
                                  ready=state == 'ready'))
        if state != 'ready':
            missing.append(entry['id'])
    python = python_status(voice_root, install_root=install_root)
    findings.append(python)
    progress = {}
    progress_file = voice_root / PROGRESS_NAME
    if progress_file.is_file():
        try:
            value = json.loads(progress_file.read_text(encoding='utf-8'))
            if isinstance(value, dict):
                progress = value
        except (OSError, json.JSONDecodeError):
            progress = {}
    severity = max((item['severity'] for item in findings), key=_severity_rank, default='info')
    return {
        'platform': platform, 'root': str(voice_root), 'components': components,
        'missing': missing,
        # Ready only when every component and the Python side are both usable.
        'ready': not missing and python.get('ready') is True,
        'progress': progress, 'findings': findings, 'severity': severity,
        'python': python,
        'paths': {item['field']: item['path'] for item in components if item['state'] == 'ready'},
    }


def _wheel_files(voice_root):
    directory = Path(voice_root) / WHEEL_DIR
    return sorted(directory.glob('*.whl')) if directory.is_dir() else []


def python_status(voice_root, *, install_root=None, runner=None):
    """Is the Python side of voice installed? Checked, never assumed.

    Without an installation to inspect the answer is `info` with the wheels
    waiting: nothing has been installed into an application runtime yet, and
    that is a normal intermediate state, not a failure.
    """
    cached = _wheel_files(voice_root)
    if not cached:
        return _finding('python', 'warning',
                        'The Python voice dependencies have not been downloaded.',
                        Path(voice_root) / WHEEL_DIR,
                        'Download voice components to fetch them.')
    interpreter = _runtime_python(install_root) if install_root else None
    if interpreter is None:
        return _finding('python', 'info',
                        f'{len(cached)} Python voice packages are downloaded and waiting for '
                        'an installed Odessy runtime.', Path(voice_root) / WHEEL_DIR, '')
    run = runner or _run
    try:
        result = run([str(interpreter), '-I', '-c',
                      'import ' + ', '.join(RUNTIME_MODULES) + ';print("ok")'], timeout=120)
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and result.returncode == 0:
        return _finding('python', 'info',
                        'The Python voice dependencies are installed and importable.',
                        Path(voice_root) / WHEEL_DIR, '', ready=True)
    detail = ''
    if result is not None and (result.stderr or '').strip():
        detail = ' ' + (result.stderr or '').strip().splitlines()[-1]
    return _finding('python', 'warning',
                    'The Python voice dependencies are downloaded but not importable in the '
                    'installed runtime.' + detail,
                    Path(voice_root) / WHEEL_DIR,
                    'Install voice dependencies for the installed Odessy runtime, or retry it.')


# ---------------------------------------------------------------------- install

def install(plan_value, *, confirm, cancelled=lambda: False):
    """Download, verify and place every offered artifact.

    A stale plan, a mismatch, a short download and an existing different file all
    stop before anything is replaced. `preserved` lists files already in place.

    The result separates three distinct facts (the shape OpenClaw's setup
    verification returns, and the reason this is not one boolean):
    `attempted` - work was started; `persisted` - files are now on disk;
    `verified` - every required component matched its pinned digest afterwards.
    """
    if confirm != plan_value.get('plan_id'):
        raise ValueError('review the exact voice installation before installing it')
    entries = plan_value.get('entries') or []
    available = plan_value.get('available') or []
    if not available:
        raise ValueError('no verified voice component is available for this build')
    if not entries:
        raise ValueError('the installation plan does not carry its manifest')
    root = Path(plan_value['root'])
    root.mkdir(parents=True, exist_ok=True)
    pending = [item for item in available if not item['installed']]
    needed = sum(item['size_bytes'] for item in pending)
    free = shutil.disk_usage(root).free
    if free < needed:
        raise ValueError(
            f'insufficient space in {root}: {needed} bytes are required and {free} are free. '
            'Free space, or choose existing voice files instead.')
    if needed and free < needed * SPACE_HEADROOM:
        # Not fatal, but the consequence must be stated before it happens rather
        # than discovered when an unrelated write fails later.
        _progress(root, 'starting',
                  message=f'Only {max(0, free - needed)} bytes will remain free after this download.',
                  total=len(pending))
    by_id = {entry['id']: entry for entry in entries}
    preserved, installed_files = [], []
    total = len(available)
    _progress(root, 'starting', message='Preparing voice components', total=total)
    for index, item in enumerate(available, start=1):
        entry = by_id.get(item['id'])
        if entry is None:
            raise ValueError('the installation plan does not match its manifest')
        _progress(root, 'downloading', message=f'{item["label"]} ({index} of {total})',
                  downloaded=index - 1, total=total, current=item['id'])
        try:
            produced = _install_one(root, entry, cancelled=cancelled)
        except _Cancelled:
            # Only our partial download is removed; a user file is never touched.
            _progress(root, 'cancelled', message='Voice installation was cancelled',
                      downloaded=index - 1, total=total)
            return {'installed': installed_files, 'preserved': preserved, 'cancelled': True,
                    'state': 'cancelled',
                    'missing': _missing(root, entries, {item['id'] for item in available}),
                    'paths': paths(root, entries), 'total_bytes': total,
                    'attempted': True, 'persisted': bool(installed_files or preserved),
                    'verified': False, 'severity': 'warning', 'wheel_count': len(_wheel_files(root)),
                    'manual_step': 'Download voice components again, or select existing files.'}
        except BaseException:
            _progress(root, 'failed', message=f'{item["label"]} was not installed',
                      downloaded=index - 1, total=total, current=item['id'])
            raise
        if produced['preserved']:
            preserved.append({'id': item['id'], 'file': entry['file'], 'path': produced['path']})
        else:
            installed_files.append({'id': item['id'], 'file': entry['file'],
                                    'path': produced['path']})
    existing = [item for item in _record(root).get('artifacts', []) if isinstance(item, dict)]
    for produced in installed_files + preserved:
        entry = by_id[produced['id']]
        paths_to_record = ([root / name for name in entry['members']]
                           if entry['kind'] == 'archive' else [Path(produced['path'])])
        for path in paths_to_record:
            digest, size = _digest_file(path)
            existing = [item for item in existing if item.get('path') != str(path)]
            existing.append({'id': produced['id'], 'path': str(path),
                             'file': path.name, 'sha256': digest, 'size_bytes': size})
    _write_json(root / RECORD_NAME, {
        'schema_version': 2,
        'installed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'root': str(root),
        'artifacts': existing,
    })
    missing = _missing(root, entries, {item['id'] for item in available})
    verified = not missing
    _progress(root, 'ready' if verified else 'incomplete',
              message='Voice components are ready' if verified else 'Voice needs attention',
              downloaded=total, total=total)
    return {'installed': installed_files, 'preserved': preserved, 'cancelled': False,
            'state': 'ready' if verified else 'incomplete', 'missing': missing,
            'paths': paths(root, entries), 'total_bytes': total,
            'attempted': True, 'persisted': bool(installed_files or preserved),
            'verified': verified,
            'severity': 'info' if verified else 'warning',
            'wheel_count': len(_wheel_files(root)),
            'manual_step': '' if verified else
                'Download the remaining components again, or select existing files.'}


def _install_one(root, entry, *, cancelled):
    """Place one artifact. Returns {'preserved': bool, 'path': str}."""
    if entry['kind'] == 'archive':
        return _install_archive(root, entry, cancelled=cancelled)
    target = _target(root, entry)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        digest, _ = _digest_file(target)
        if digest == entry['sha256']:
            return {'preserved': True, 'path': str(target)}
        raise ValueError(f'{target.name} already exists with different contents; '
                         'it was not replaced')
    partial = target.with_name(target.name + '.partial')
    try:
        _download(entry, partial, root, cancelled)
        digest, size = _digest_file(partial)
        if size != entry['size_bytes'] or digest != entry['sha256']:
            raise ValueError(f'{entry["label"]} failed verification and was discarded')
        os.replace(partial, target)
    except BaseException:
        if partial.is_file():
            partial.unlink(missing_ok=True)
        raise
    return {'preserved': False, 'path': str(target)}


def _install_archive(root, entry, *, cancelled):
    """Download a pinned ZIP and extract only its allow-listed members."""
    root = Path(root)
    if _present(root, entry):
        return {'preserved': True, 'path': str(root / entry['executable'])}
    partial = root / (entry['file'] + '.partial')
    try:
        _download(entry, partial, root, cancelled)
        digest, size = _digest_file(partial)
        if size != entry['size_bytes'] or digest != entry['sha256']:
            raise ValueError(f'{entry["label"]} failed verification and was discarded')
        staged = {}
        with zipfile.ZipFile(partial) as archive:
            names = set(archive.namelist())
            budget = entry['size_bytes'] * 4
            used = 0
            for member in entry['members']:
                source = (entry['strip_prefix'] + member) if entry['strip_prefix'] else member
                if source not in names:
                    raise ValueError(f'{entry["label"]} does not contain {member}')
                info = archive.getinfo(source)
                if info.is_dir():
                    raise ValueError(f'{entry["label"]} has an unexpected directory entry')
                used += info.file_size
                if used > budget:
                    raise ValueError(f'{entry["label"]} unpacks larger than expected')
                # `member` was validated as a bare file name, and `source` came from
                # this archive, so extraction cannot escape the voice directory.
                staged[member] = archive.read(source)
        for member, data in staged.items():
            destination = root / member
            if destination.is_file():
                digest, _ = _digest_file(destination)
                if digest == hashlib.sha256(data).hexdigest():
                    continue
                raise ValueError(f'{destination.name} already exists with different contents; '
                                 'it was not replaced')
            temporary = destination.with_name(destination.name + '.partial')
            temporary.write_bytes(data)
            os.replace(temporary, destination)
    finally:
        # Success or failure, the downloaded archive itself is never kept: only
        # the extracted members belong in the voice directory.
        if partial.is_file():
            partial.unlink(missing_ok=True)
    return {'preserved': False, 'path': str(root / entry['executable'])}


def _download(item, partial, root, cancelled):
    scheme = urllib.parse.urlparse(item['url']).scheme.lower()
    written = 0
    try:
        response = urllib.request.urlopen(item['url'], timeout=DEFAULT_TIMEOUT)
    except (urllib.error.URLError, OSError) as error:
        raise ValueError(
            f'{item["label"]} could not be downloaded: {error}. '
            'Check the internet connection, then retry.') from None
    with response:
        declared = response.headers.get('Content-Length') if scheme != 'file' else None
        if declared is not None:
            if not declared.isdigit() or int(declared) != item['size_bytes']:
                raise ValueError(
                    f'{item["label"]} is not the published size; nothing was installed')
        with open(partial, 'wb') as handle:
            while True:
                if cancelled():
                    raise _Cancelled()
                block = response.read(CHUNK)
                if not block:
                    break
                written += len(block)
                if written > item['size_bytes']:
                    raise ValueError(f'{item["label"]} is larger than published; discarded')
                handle.write(block)
                _progress(root, 'downloading', message=f'Downloading {item["label"]}',
                          downloaded=written, total=item['size_bytes'], current=item['id'])
    if written != item['size_bytes']:
        raise ValueError(f'{item["label"]} stopped before the published size; discarded')


# ------------------------------------------------------------------ python part

def _run(argv, *, timeout=PIP_TIMEOUT, env=None):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env,
                          **({'creationflags': subprocess.CREATE_NO_WINDOW}
                             if os.name == 'nt' else {}))


def _runtime_python(install_root):
    """The interpreter of the active installation, or None.

    The packaged installer builds `<root>/versions/<version>` as a venv, so the
    application imports from that environment's site-packages, not the shared
    runtime's.
    """
    if not install_root:
        return None
    root = Path(install_root)
    try:
        record = json.loads((root / 'installation.json').read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    active = record.get('active')
    if not isinstance(active, str) or not active:
        return None
    relative = (Path('Scripts') / 'python.exe') if os.name == 'nt' else (Path('bin') / 'python3')
    candidate = root / 'versions' / active / relative
    return candidate if candidate.is_file() else None


def _wheel_pin(filename):
    """`name==version` from a wheel file name, ignoring any build tag."""
    base = filename[:-4].split('-')
    return f'{base[0]}=={base[1]}'


def requirements_text(entries):
    """A hash-locked requirement file for the downloaded wheels."""
    lines = ['# Generated by the Odessy installer; every line is hash-locked.']
    for entry in sorted(entries, key=lambda item: item['id']):
        if entry['kind'] == 'wheel':
            lines.append(f'{_wheel_pin(entry["file"])} --hash=sha256:{entry["sha256"]}')
    return '\n'.join(lines) + '\n'


def python_install(install_root, voice_root, *, entries=None, payload=None,
                   cancelled=lambda: False, runner=None):
    """Install the cached wheels into the installed application runtime.

    Offline and hash-locked: `--no-index --no-deps --require-hashes` with the
    wheel cache as the only source, so a compromised index cannot substitute a
    package. Readiness is only reported after the interpreter actually imports
    the modules the application needs.
    """
    root = Path(voice_root)
    wheels = _wheel_files(root)
    if not wheels:
        raise ValueError('voice components have not been downloaded yet')
    interpreter = _runtime_python(install_root)
    if interpreter is None:
        raise ValueError('no installed Odessy runtime was found to install voice into')
    all_entries = entries if entries is not None else load_manifest(manifest_path(payload))
    wheel_entries = [entry for entry in all_entries if entry['kind'] == 'wheel']
    expected = {entry['file'] for entry in wheel_entries}
    if expected and not expected <= {path.name for path in wheels}:
        raise ValueError('the downloaded wheel set does not match the pinned manifest')
    if cancelled():
        raise ValueError('cancelled')
    requirement = root / WHEEL_DIR / REQUIREMENTS_NAME
    requirement.write_text(requirements_text(wheel_entries), encoding='utf-8')
    run = runner or _run
    _progress(root, 'python', message='Installing Python voice dependencies', total=1)
    result = run([str(interpreter), '-I', '-m', 'pip', 'install', '--no-index', '--no-deps',
                  '--disable-pip-version-check', '--find-links', str(root / WHEEL_DIR),
                  '--require-hashes', '-r', str(requirement)])
    log = [line for line in ((result.stdout or '') + (result.stderr or '')).splitlines() if line]
    if result.returncode != 0:
        _progress(root, 'python-failed', message='Python voice dependencies failed to install')
        return {'installed': False, 'verified': False, 'severity': 'error',
                'python': str(interpreter), 'wheels': len(wheels), 'log': log[-25:],
                'manual_step': 'Retry the dependency install, or continue with text only.'}
    check = run([str(interpreter), '-I', '-c',
                 'import ' + ', '.join(RUNTIME_MODULES) + ';print("ok")'])
    verified = check.returncode == 0
    _progress(root, 'ready' if verified else 'python-failed',
              message=('Voice is ready' if verified else
                       'Python voice dependencies installed but could not be imported'))
    return {'installed': True, 'verified': verified,
            'severity': 'info' if verified else 'error',
            'python': str(interpreter), 'wheels': len(wheels), 'log': log[-25:],
            'imports': (check.stdout or '').strip(),
            'manual_step': '' if verified else
                'Retry the dependency install; the packages are installed but not importable.'}


def dispatch(parameters, *, cancelled=lambda: False):
    """Entry point used by the setup bridge. No path arrives from the caller."""
    action = parameters.get('action')
    payload = parameters.get('payload') or None
    root = default_root()
    entries = load_manifest(manifest_path(payload))
    if action == 'voice-plan':
        return plan(payload, manifest=entries, root=root)
    if action == 'voice-status':
        return status(payload, manifest=entries, root=root,
                      install_root=parameters.get('install_root') or None)
    if action == 'voice-install':
        value = plan(payload, manifest=entries, root=root)
        value['entries'] = entries
        return install(value, confirm=parameters.get('confirm'), cancelled=cancelled)
    if action == 'voice-python-install':
        return python_install(parameters.get('install_root'), root, entries=entries,
                              cancelled=cancelled)
    raise ValueError('unsupported voice operation')
