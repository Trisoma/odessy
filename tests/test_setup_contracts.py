"""The setup contracts must stay importable from the offline bootstrap alone."""
from __future__ import annotations
import builtins

import pytest

from odessy.setup import contracts


def test_voice_selection_needs_no_part_of_the_voice_package(monkeypatch):
    """A packaged installer ships only `odessy.setup`, so nothing may reach out.

    Importing `odessy.voice.providers` here used to abort every review with
    "No module named 'odessy.voice'" before a single file was written.
    """
    real_import = builtins.__import__

    def guard(name, *args, **kwargs):
        if name == 'odessy.voice' or name.startswith('odessy.voice.'):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guard)
    selection = contracts.SetupSelection.from_dict(
        {'role': 'combined', 'voice': {'enabled': False}})
    assert selection.voice.enabled is False
    enabled = contracts.SetupSelection.from_dict(
        {'role': 'combined', 'voice': {'enabled': True,
                                       'stt_provider': 'pkg.module:factory'}})
    assert enabled.voice.stt_provider == 'pkg.module:factory'


def test_the_local_selector_check_matches_the_voice_package():
    """Two implementations of one rule, so pin them to each other."""
    from odessy.voice.providers import validate_selector as authoritative

    for value in ['whisper.cpp', 'kokoro', 'pkg.module:factory',
                  'pkg.module', 'x', '', 'bad name', '9bad', 'a:b:c', 'a:b']:
        try:
            expected = authoritative(value)
        except ValueError:
            expected = None
        try:
            actual = contracts.validate_selector(value)
        except ValueError:
            actual = None
        assert actual == expected, value


def test_an_invalid_selector_is_rejected_with_one_message():
    with pytest.raises(ValueError, match='speech provider must be'):
        contracts.SetupSelection.from_dict(
            {'role': 'combined', 'voice': {'enabled': True,
                                           'stt_provider': 'not a name'}})


def test_the_review_path_never_imports_the_voice_package():
    """One stray import anywhere in the chain breaks a packaged review."""
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import odessy.setup.native_bridge\n"
        "import odessy.setup.package_context\n"
        "from odessy.setup.contracts import SetupSelection\n"
        "SetupSelection.from_dict({'role':'combined','voice':{'enabled':False}})\n"
        "loaded=sorted(n for n in sys.modules if n=='odessy.voice' or n.startswith('odessy.voice.'))\n"
        "print(loaded)\n"
        "raise SystemExit(1 if loaded else 0)\n"
    )
    result = subprocess.run([sys.executable, '-c', code],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
