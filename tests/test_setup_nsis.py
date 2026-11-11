"""The native bootstrap: single-layer script, safe members, and a compiler that cannot
block on an unread pipe (an interrupted executable must never look like a build).
"""
import importlib.util
from pathlib import Path
import sys
import zipfile

import pytest


def module():
    spec = importlib.util.spec_from_file_location(
        'windows_nsis', Path(__file__).parents[1] / 'tool/windows_nsis.py')
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_native_script_is_single_layer_and_per_user(tmp_path):
    script = module().script(tmp_path / 'files', tmp_path / 'Setup.exe', tmp_path / 'icon.ico')
    assert 'RequestExecutionLevel user' in script
    assert 'Unicode true' in script
    assert 'CRCCheck force' in script
    assert 'Page instfiles' in script
    assert 'ODESSY_SETUP_PAYLOAD' in script
    assert '--setup' in script
    # The Flutter setup receives the staged payload root explicitly; it only
    # auto-discovers a `payload/` folder beside the executable.
    assert '--payload "$INSTDIR"' in script
    assert 'payload.zip' not in script and 'powershell' not in script.lower()
    assert 'RMDir /r' not in script


def test_windows_extraction_rejects_case_collisions_and_aliases():
    value = module()
    with pytest.raises(ValueError):
        value.validate_members([zipfile.ZipInfo('desktop/App.exe'), zipfile.ZipInfo('desktop/app.exe')])
    for name in ('desktop/app.exe.', '../outside', 'desktop/CON', 'desktop/app:stream'):
        with pytest.raises(ValueError):
            value.validate_members([zipfile.ZipInfo(name)])


FAKE_COMPILER = '''\
import os, re, sys
nsi = open(sys.argv[-1], encoding="utf-8-sig").read()
target = re.search(r'OutFile "([^"]+)"', nsi).group(1)
mode = os.environ.get("FAKE_NSIS_MODE", "ok")
# More than a pipe buffer, so a parent that does not read would block here.
for index in range(20000):
    print("File: file-%05d" % index)
if mode == "slow":
    import time
    time.sleep(30)
if mode == "garbage":
    open(target, "wb").write(b"NOT-A-PE")
    sys.exit(0)
if mode in ("fail", "partial-fail"):
    if mode == "partial-fail":
        open(target, "wb").write(b"MZ" + b"\\0" * 512)
    print("Error: could not open input file")
    sys.exit(2)
open(target, "wb").write(b"MZ" + b"\\0" * 4096)
sys.exit(0)
'''


def _fake_root(tmp_path, mode):
    compiler = tmp_path / 'fake_makensis.py'
    compiler.write_text(FAKE_COMPILER, encoding='utf-8')
    source = tmp_path / 'OdessySetup.nsi'
    source.write_text('OutFile "%s"\n' % (tmp_path / 'Setup.exe.incomplete'), encoding='utf-8')
    return [sys.executable, str(compiler)], source, tmp_path / 'Setup.exe', mode


def test_compiler_output_is_drained_and_the_artifact_published(tmp_path, monkeypatch):
    command, source, output, mode = _fake_root(tmp_path, 'ok')
    monkeypatch.setenv('FAKE_NSIS_MODE', mode)
    log = tmp_path / 'makensis.log'

    result = module().compile_installer(source, output, compiler_path=command, log_path=log)

    assert result == output and output.is_file() and output.read_bytes()[:2] == b'MZ'
    # 20 000 lines: fully consumed, and kept for diagnosis.
    assert len(log.read_text(encoding='utf-8').splitlines()) == 20000
    assert not (tmp_path / 'Setup.exe.incomplete').exists()


def test_a_failed_compile_is_reported_and_never_published(tmp_path, monkeypatch):
    command, source, output, mode = _fake_root(tmp_path, 'fail')
    monkeypatch.setenv('FAKE_NSIS_MODE', mode)

    with pytest.raises(RuntimeError, match='exit 2'):
        module().compile_installer(source, output, compiler_path=command,
                                   log_path=tmp_path / 'makensis.log')
    # A failed compile never creates the release name, and its output stays on disk.
    assert not output.exists()
    assert 'could not open input file' in (tmp_path / 'makensis.log').read_text(encoding='utf-8')


def test_an_interrupted_compile_keeps_the_partial_but_never_publishes(tmp_path, monkeypatch):
    command, source, output, mode = _fake_root(tmp_path, 'partial-fail')
    monkeypatch.setenv('FAKE_NSIS_MODE', mode)

    with pytest.raises(RuntimeError, match='exit 2'):
        module().compile_installer(source, output, compiler_path=command,
                                   log_path=tmp_path / 'makensis.log')
    assert not output.exists()
    assert (tmp_path / 'Setup.exe.incomplete').is_file()


def test_an_output_without_the_executable_signature_is_refused(tmp_path, monkeypatch):
    command, source, output, mode = _fake_root(tmp_path, 'garbage')
    monkeypatch.setenv('FAKE_NSIS_MODE', mode)

    with pytest.raises(RuntimeError, match='not a Windows executable'):
        module().compile_installer(source, output, compiler_path=command,
                                   log_path=tmp_path / 'makensis.log')
    assert not output.exists()


def test_a_compile_that_exceeds_its_bound_is_reported_as_slow_not_failed(tmp_path, monkeypatch):
    command, source, output, mode = _fake_root(tmp_path, 'slow')
    monkeypatch.setenv('FAKE_NSIS_MODE', mode)

    with pytest.raises(TimeoutError, match='exceeded'):
        module().compile_installer(source, output, compiler_path=command,
                                   log_path=tmp_path / 'makensis.log', timeout=2)
    assert not output.exists()
