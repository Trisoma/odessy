"""Exercise the interpreter and host-build checks used by the Linux builder."""
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'tool/linux/build_setup_payload.sh'


def check_code(marker):
    line = next(line for line in SCRIPT.read_text().splitlines() if marker in line)
    return line.split("-c '", 1)[1].split("'", 1)[0]


@pytest.mark.parametrize('implementation,version,system,machine,expected', [
    ('CPython', (3, 10), 'linux', 'x86_64', True),
    ('CPython', (3, 11), 'linux', 'x86_64', False),
    ('PyPy', (3, 10), 'linux', 'x86_64', False),
    ('CPython', (3, 10), 'linux', 'aarch64', False),
    ('CPython', (3, 10), 'win32', 'x86_64', False),
])
def test_runtime_validation(implementation, version, system, machine, expected):
    setup = (f'import platform,sys; platform.python_implementation=lambda:{implementation!r}; '
             f'platform.machine=lambda:{machine!r}; sys.version_info={version!r}; '
             f'sys.platform={system!r}; ')
    result = subprocess.run([sys.executable, '-c', setup + check_code('platform.python_implementation')])
    assert (result.returncode == 0) is expected


@pytest.mark.parametrize('version,expected', [('67.9', False), ('68.0', True), ('70.1', True), ('68.0rc1', False)])
def test_setuptools_minimum(version, expected):
    setup = f'import sys,types; sys.modules["setuptools"]=types.SimpleNamespace(__version__={version!r}); '
    result = subprocess.run([sys.executable, '-c', setup + check_code('setuptools.__version__')])
    assert (result.returncode == 0) is expected
