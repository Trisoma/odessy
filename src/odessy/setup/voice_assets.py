"""Bounded observation of existing speech assets. Never installs or runs engines."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import time

KEYS = ('whisper_bin', 'whisper_model', 'kokoro_model', 'kokoro_voices')


def discover_voice_assets(selection):
    home = Path.home()
    if not isinstance(selection, dict): raise ValueError('Invalid voice selection.')
    voice = selection.get('voice') or {}
    if not isinstance(voice, dict): raise ValueError('Invalid voice settings.')
    candidates = {name: [] for name in KEYS}

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

    for name in KEYS:
        add(name, voice.get(name) or os.environ.get('ODESSY_' + name.upper()))
    add('whisper_bin', shutil.which('whisper-cli'))
    add('whisper_bin', home/'whisper.cpp/build/bin/whisper-cli')
    roots = [home/'models', home/'odessy-voice-test', home/'whisper.cpp/models', home/'odessy/models', home/'odessy/voice', home/'.local/share/odessy']
    seen = 0
    deadline = time.monotonic() + 2
    truncated = False
    for root in roots:
        if time.monotonic() >= deadline:
            truncated = True; break
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in ('node_modules', 'build', 'cache'))
            if len(Path(directory).relative_to(root).parts) >= 3: dirs[:] = []
            for file in sorted(files):
                seen += 1
                if seen > 4000 or time.monotonic() >= deadline:
                    truncated = True; break
                path = Path(directory)/file
                if file.startswith('ggml-') and file.endswith('.bin'): add('whisper_model', path)
                if file.startswith('kokoro') and file.endswith('.onnx'): add('kokoro_model', path)
                if file.startswith('voices') and file.endswith('.bin'): add('kokoro_voices', path)
            if truncated: break
        if truncated: break
    return {'candidates': candidates, 'missing': [name for name, values in candidates.items() if not values],
            'partial': truncated, 'reason': 'Existing files only; no inference, downloads or audio capture performed.'}
