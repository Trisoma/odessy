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


def test_setuptools_check_without_vendored_pip():
    setup = ('import sys,types,importlib.machinery; Version=lambda value: tuple(int(part) for part in value.split(".")); '
             'sys.modules["setuptools"]=types.SimpleNamespace(__version__="68.0"); '
             'sys.modules["packaging"]=types.ModuleType("packaging"); '
             'sys.modules["packaging"].__spec__=importlib.machinery.ModuleSpec("packaging",None,is_package=True); '
             'sys.modules["packaging.version"]=types.SimpleNamespace(Version=Version); '
             'sys.modules["pip._vendor.packaging.version"]=None; ')
    result = subprocess.run([sys.executable, '-c', setup + check_code('setuptools.__version__')])
    assert result.returncode == 0


def test_setuptools_check_with_only_vendored_packaging():
    setup = ('import sys,types,importlib.util; '
             'sys.modules["setuptools"]=types.SimpleNamespace(__version__="68.0"); '
             'sys.modules["pip"]=types.ModuleType("pip"); sys.modules["pip"].__path__=[]; '
             'sys.modules["pip._vendor"]=types.ModuleType("pip._vendor"); sys.modules["pip._vendor"].__path__=[]; '
             'sys.modules["pip._vendor.packaging"]=types.ModuleType("pip._vendor.packaging"); '
             'sys.modules["pip._vendor.packaging"].__path__=[]; '
             'version=types.ModuleType("pip._vendor.packaging.version"); '
             'version.Version=lambda value: tuple(int(part) for part in value.split(".")); '
             'sys.modules["pip._vendor.packaging.version"]=version; '
             'original=importlib.util.find_spec; '
             'importlib.util.find_spec=lambda name: None if name=="packaging" else original(name); ')
    result = subprocess.run([sys.executable, '-c', setup + check_code('setuptools.__version__')])
    assert result.returncode == 0
