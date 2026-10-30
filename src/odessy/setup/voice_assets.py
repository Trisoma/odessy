"""Bounded observation of existing speech assets. Never installs or runs engines."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import time

#: Assets a component needs before it can be configured at all. `missing` is reported
#: over exactly these, so observing an optional asset cannot make a complete STT/TTS
#: configuration look incomplete.
REQUIRED_KEYS = ('whisper_bin', 'whisper_model', 'kokoro_model', 'kokoro_voices')

#: Everything reported under `candidates`, including the optional wake model.
KEYS = REQUIRED_KEYS + ('wake_model',)

SEARCH_SECONDS = 2
MAX_ENTRIES = 4000
MAX_DEPTH = 3

def wake_directory():
    """Mirrors odessy.voice.assets.DEFAULT_WAKE_ASSET_DIR.

    Computed at call time, not module import, so a changed HOME is honoured. The
    two are asserted equal by the tests to catch drift.
    """
    return Path.home() / '.local' / 'share' / 'odessy' / 'wake'


#: Fields whose value must be a runnable file rather than data.
EXECUTABLE_KEYS = ('whisper_bin', 'stt_executable', 'tts_executable')

#: Fields offered a detected-file selector, in the order the UI shows them.
CHECKED_KEYS = ('whisper_bin', 'whisper_model', 'kokoro_model', 'kokoro_voices',
                'stt_executable', 'stt_model', 'tts_executable', 'tts_model', 'wake_model')


def _looks_like_path(value):
    """A bare word may be a command resolved on PATH; it is not a filesystem path."""
    decorated = value.startswith(('~', '.')) or value.startswith('/') or value.startswith('\\')
    return decorated or '/' in value or '\\' in value or (len(value) > 1 and value[1] == ':')


def _inspect(key, value):
    """One field. Returns None or a (message, blocking) pair."""
    if key in EXECUTABLE_KEYS and not _looks_like_path(value):
        # Existing configurations name the binary on PATH; that is legitimate.
        return None if shutil.which(value) else (
            'Executable not found on PATH. Check it before saving or testing.', False)
    try:
        path = Path(value).expanduser()
        if path.is_dir():
            if key == 'whisper_bin':
                return 'Select the Whisper executable, not a folder.', True
            if key in EXECUTABLE_KEYS:
                return 'Select an executable file, not a folder.', True
            return 'Select a file, not a folder.', True
        if not path.is_file():
            return ('Executable not found. Check this path before saving or testing.' if key in EXECUTABLE_KEYS
                    else 'File not found. Check this path before saving or testing.'), False
        if key in EXECUTABLE_KEYS and os.name != 'nt' and not os.access(path, os.X_OK):
            return 'This file exists but is not executable.', True
    except OSError:
        return None
    return None


def check_voice_paths(selection):
    """Field-specific path checks for the speech assets, performed by the helper.

    The desktop UI cannot stat the filesystem (its presentation layer has no I/O), so
    the same process that will consume a path is the one that judges it. Blocking
    issues are wrong-kind-of-thing problems (a folder where a file belongs, or a
    binary without the execute bit); a missing path is only an advisory, because a
    volume may not be mounted yet.
    """
    if not isinstance(selection, dict):
        raise ValueError('Invalid voice selection.')
    voice = selection.get('voice') or {}
    if not isinstance(voice, dict):
        raise ValueError('Invalid voice settings.')
    issues = {}
    for key in CHECKED_KEYS:
        value = voice.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        outcome = _inspect(key, value.strip())
        if outcome:
            issues[key] = {'message': outcome[0], 'blocking': outcome[1]}
    return issues


def _inside(path, root):
    try:
        return root in path.resolve().parents
    except OSError:
        return False


def discover_voice_assets(selection):
    home = Path.home()
    if not isinstance(selection, dict): raise ValueError('Invalid voice selection.')
    voice = selection.get('voice') or {}
    if not isinstance(voice, dict): raise ValueError('Invalid voice settings.')
    candidates = {name: [] for name in KEYS}
    wake_root = wake_directory()

    def add(name, value):
        if not value:
            return
        try:
            path = Path(value).expanduser()
            if path.is_file():
                value = str(path.resolve())
                if value not in candidates[name]: candidates[name].append(value)
        except OSError:
            pass

    # Configured values come first, then the matching environment variable: a path in
    # use today is more useful than another candidate, and both are official settings.
    for name in KEYS:
        add(name, voice.get(name))
        add(name, os.environ.get('ODESSY_' + name.upper()))
    add('whisper_bin', shutil.which('whisper-cli'))
    add('whisper_bin', home/'whisper.cpp/build/bin/whisper-cli')
    roots = [home/'models', home/'odessy-voice-test', home/'whisper.cpp/models', home/'odessy/models', home/'odessy/voice', home/'.local/share/odessy',
             wake_root, home/'openwakeword', home/'openWakeWord']
    seen = 0
    deadline = time.monotonic() + SEARCH_SECONDS
    truncated = False
    for root in roots:
        if time.monotonic() >= deadline:
            truncated = True; break
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in ('node_modules', 'build', 'cache'))
            if len(Path(directory).relative_to(root).parts) >= MAX_DEPTH: dirs[:] = []
            for file in sorted(files):
                seen += 1
                if seen > MAX_ENTRIES or time.monotonic() >= deadline:
                    truncated = True; break
                path = Path(directory)/file
                if file.startswith('ggml-') and file.endswith('.bin'): add('whisper_model', path)
                if file.startswith('kokoro') and file.endswith('.onnx'): add('kokoro_model', path)
                if file.startswith('voices') and file.endswith('.bin'): add('kokoro_voices', path)
                # openWakeWord ships `hey_<phrase>_v<version>.onnx`. Anything already inside
                # the wake asset directory is a wake model by construction.
                if file.endswith('.onnx') and not file.startswith('kokoro'):
                    if file.startswith('hey_') or _inside(path, wake_root): add('wake_model', path)
            if truncated: break
        if truncated: break
    return {'candidates': candidates, 'missing': [name for name in REQUIRED_KEYS if not candidates[name]],
            'partial': truncated, 'reason': 'Existing files only; no inference, downloads or audio capture performed.'}
