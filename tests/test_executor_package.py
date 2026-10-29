"""Executor packaging must never erase an existing directory."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.skipif(shutil.which('pwsh') is None, reason='PowerShell 7 required')
def test_executor_package_preserves_existing_directory_even_with_checksums(tmp_path):
    output = tmp_path / 'existing'
    output.mkdir()
    (output / 'checksums.txt').write_text('not an ownership token')
    sentinel = output / 'user-file.txt'
    sentinel.write_text('keep me')
    node = tmp_path / 'node'
    node.mkdir()
    server = tmp_path / 'server.js'
    server.write_text('placeholder')
    result = subprocess.run([
        'pwsh', '-NoProfile', '-File', str(ROOT / 'native/odessy-link/package-executor.ps1'),
        '-NodeDirectory', str(node), '-NodeServer', str(server),
        '-OutputDirectory', str(output), '-SkipBuild',
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert sentinel.read_text() == 'keep me'
    assert 'output already exists' in result.stdout + result.stderr
