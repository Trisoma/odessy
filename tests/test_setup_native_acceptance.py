import sys
import importlib.util
from contextlib import contextmanager
from pathlib import Path

import pytest

from odessy.setup.maintenance import guard

_script = Path(__file__).resolve().parents[1] / 'tool' / 'check_setup_installation.py'
_spec = importlib.util.spec_from_file_location('check_setup_installation', _script)
check_setup_installation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_setup_installation)


def test_acceptance_resolves_linked_system_temp_parent(tmp_path, monkeypatch):
    actual = tmp_path / 'actual'
    actual.mkdir()
    link = tmp_path / 'linked-temp'
    try:
        link.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip('Creating directory symlinks is unavailable')
    wheelhouse = tmp_path / 'wheels'
    wheelhouse.mkdir()

    @contextmanager
    def temporary_directory(**kwargs):
        yield str(link)

    class ReachedManager(Exception):
        pass

    def manager(root):
        assert root == actual.resolve() / 'runtime'
        assert guard(root) == root
        raise ReachedManager

    monkeypatch.setattr(check_setup_installation.tempfile, 'TemporaryDirectory', temporary_directory)
    monkeypatch.setattr(check_setup_installation, 'ManagedInstallation', manager)
    monkeypatch.setattr(sys, 'argv', ['check_setup_installation.py', '--wheelhouse', str(wheelhouse)])
    with pytest.raises(ReachedManager):
        check_setup_installation.main()
